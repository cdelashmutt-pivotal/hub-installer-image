#!/usr/bin/env python3
"""Small client for the Hub for TrueSource GraphQL API (local evaluation installs).

Uses the same GraphQL operations as the Hub UI. Reads credentials from files next to this script:
  .hub-admin      HUB_USER, HUB_PASSWORD
  .github-oauth   GITHUB_CLIENT_ID, GITHUB_CLIENT_SECRET
  .broadcom-creds BROADCOM_USER, BROADCOM_TOKEN (registry token), BROADCOM_REFRESH_TOKEN (API refresh token)
Secrets never go on the command line or to stdout.

Commands:
  login                     check the admin login
  broadcom                  make sure Hub has a valid Broadcom refresh token (stores BROADCOM_REFRESH_TOKEN if not)
  git-provider              register the GitHub OAuth app (creates or updates)
  git-status                show whether the admin has connected a GitHub account
  checklist STEP...         mark Spring Enterprise setup steps done (short names below)
  repo URL [GROUP]          add a GitHub repo to a repository group (default "My Spring Apps") and assess it
  assessments [GROUP]       show the latest assessment task status
  catalog-status            Secure Asset Catalog library count and indexer state
  catalog-slim OUT [SEC..]  build a smaller catalog zip from Hub's Broadcom download (default: library-catalog)
  catalog-load ZIP          upload a catalog zip and have Hub index it
  catalog-refresh           ask Hub to check Broadcom for a new catalog release
  catalog-fix               if the catalog is empty: load just the library section from Hub's download
  advisor-account           create the Spring Insights service account (.advisor-sa) for advisor-publish.sh

Checklist short names: prerequisites, broadcom-token, maven, git, identity-provider, invite-team
"""
import base64, json, os, ssl, sys, time, urllib.parse, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
HUB = os.environ.get("HUB_URL", "https://hub.localtest.me")
CA = os.path.join(HERE, "work", "hubca", "hub-ca.pem")
UI_CLIENT = ("tp_app", "tanzu_intentionally_not_a_secret")  # Hub UI's public OAuth client (product default)
GITHUB_BASE = "https://github.com"
CALLBACK = HUB + "/hub/application-portfolio/scm-oauth-callback"

FLOW = "springEnterprise"
STEPS = {  # checklist order in the Hub UI
    "prerequisites": "springEnterprise.collectPrerequisites",
    "broadcom-token": "deploy-apps-tanzu-net-token",
    "maven": "springEnterprise.configureMavenRepository",
    "git": "springEnterprise.addGitIntegration",
    "identity-provider": "springEnterprise.addIdentityProvider",
    "invite-team": "springEnterprise.inviteYourTeam",
}


def die(msg):
    print("ERROR: " + msg, file=sys.stderr)
    sys.exit(1)


ENV_ALIASES = {"HUB_USER": "TANZU_HUB_USERNAME"}  # same value, different name in a consolidated .envrc


def read_env(name):
    path = os.path.join(HERE, name)
    if not os.path.exists(path):
        known = KNOWN_KEYS.get(name, set())
        out = {k: os.environ[ENV_ALIASES.get(k, k)] for k in known if ENV_ALIASES.get(k, k) in os.environ}
        if not out:
            die(f"{path} is missing and none of {', '.join(sorted(known))} are set in the environment")
        return out
    out = {}
    for line in open(path):
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            out[k.strip().removeprefix("export ").strip()] = v.strip().strip("'\"")
    known = KNOWN_KEYS.get(name)
    if known:
        for k in out:
            if k not in known:
                print(f"WARNING: {name} has an unknown setting '{k}' (expected: {', '.join(sorted(known))}). "
                      "Check the spelling.", file=sys.stderr)
    return out


KNOWN_KEYS = {
    ".broadcom-creds": {"BROADCOM_USER", "BROADCOM_TOKEN", "BROADCOM_REFRESH_TOKEN"},
    ".hub-admin": {"HUB_USER", "HUB_PASSWORD"},
    ".github-oauth": {"GITHUB_CLIENT_ID", "GITHUB_CLIENT_SECRET"},
}


CTX = None
if os.path.exists(CA):
    CTX = ssl.create_default_context(cafile=CA)
    # Python 3.13+ strict mode rejects Hub's self-signed root CA (no keyUsage extension).
    CTX.verify_flags &= ~ssl.VERIFY_X509_STRICT


def http(url, data=None, headers=None):
    url = urllib.parse.quote(url, safe=":/?&=%#+,;@")  # Hub's upload links don't encode spaces
    req = urllib.request.Request(url, data=data, headers=headers or {})
    try:
        with urllib.request.urlopen(req, context=CTX, timeout=60) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()
    except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
        # Hub (or Docker Desktop) is down or restarting: report it like a 5xx so callers can retry
        return 599, str(getattr(e, "reason", e)).encode()


_token = None


def token():
    global _token
    if _token:
        return _token
    creds = read_env(".hub-admin")
    body = urllib.parse.urlencode({"grant_type": "password", "username": creds["HUB_USER"],
                                   "password": creds["HUB_PASSWORD"]}).encode()
    basic = base64.b64encode(":".join(UI_CLIENT).encode()).decode()
    for attempt in range(4):
        code, raw = http(HUB + "/auth/oauth/token", body,
                         {"Authorization": "Basic " + basic, "Content-Type": "application/x-www-form-urlencoded"})
        if code < 500:
            break
        # 502/503 "no healthy upstream": a Hub service is restarting. Not a password problem, so wait and retry.
        time.sleep(15)
    if code >= 500:
        die(f"Hub is not answering (HTTP {code}): {raw[:120].decode(errors='replace')}. A Hub service is probably "
            "restarting; try again in a minute (kubectl get pods -n tanzusm).")
    if code != 200:
        die(f"Hub login failed (HTTP {code}): {raw[:200].decode(errors='replace')}. "
            "Check HUB_PASSWORD in .hub-admin (the password you set at first sign-in). "
            "Hub locks the account for 5 minutes after 5 failures.")
    _token = json.loads(raw)["access_token"]
    return _token


def claims():
    p = token().split(".")[1]
    return json.loads(base64.urlsafe_b64decode(p + "=" * (-len(p) % 4)))


def gql(query, variables=None):
    body = json.dumps({"query": query, "variables": variables or {}}).encode()
    code, raw = http(HUB + "/hub/graphql", body,
                     {"Authorization": "Bearer " + token(), "Content-Type": "application/json"})
    if code != 200:
        die(f"GraphQL HTTP {code}: {raw[:300].decode(errors='replace')}")
    d = json.loads(raw)
    if d.get("errors"):
        die("GraphQL error: " + "; ".join(e.get("message", "?") for e in d["errors"]))
    return d["data"]


def props(entity):
    return {p["name"]: (p["value"][0] if isinstance(p["value"], list) and p["value"] else p["value"])
            for p in entity.get("properties") or []}


# ---------------------------------------------------------------- commands

def cmd_login(_):
    c = claims()
    print(f"Hub login OK as {c.get('user_name')} ({c.get('email') or 'no email'})")


def cmd_broadcom(_):
    q = "{ warehouseQuery { indexerState { warehouseTokenValid } } }"
    if gql(q)["warehouseQuery"]["indexerState"]["warehouseTokenValid"]:
        print("Broadcom refresh token: already stored in Hub and valid")
        return
    # Hub checks this token at network.tanzu.vmware.com (/api/v2/authentication/access_tokens), so it must be
    # the Broadcom API refresh token. The packages.broadcom.com registry token is always rejected here.
    tok = read_env(".broadcom-creds").get("BROADCOM_REFRESH_TOKEN")
    if not tok:
        die("BROADCOM_REFRESH_TOKEN is not set in .broadcom-creds (see .broadcom-creds.example and the README, "
            "'What you need from the Broadcom Support Portal'). The registry token does not work for this step.")
    m = """mutation($input: WarehouseCredentialsValidationInput) { warehouseMutation {
             validateWarehouseCredentials(input: $input) { isValid httpCode message } } }"""
    r = gql(m, {"input": {"token": tok, "storeCredentialIfTokenIsValid": True}})
    r = r["warehouseMutation"]["validateWarehouseCredentials"]
    if not r["isValid"]:
        die(f"Broadcom refresh token rejected (HTTP {r.get('httpCode')}): {r.get('message')}. "
            "Check BROADCOM_REFRESH_TOKEN in .broadcom-creds: it must be the API refresh token, not the registry token.")
    print("Broadcom refresh token: validated and stored in Hub")


def scm_providers():
    q = """{ entityQuery { queryEntities(entityType: "Tanzu.Portfolio.ScmProvider", first: 50) {
             entities { entityId entityName properties(name: ["Provider", "BaseUrl", "ClientId"]) { name value } } } } }"""
    return gql(q)["entityQuery"]["queryEntities"]["entities"]


def github_provider():
    for e in scm_providers():
        p = props(e)
        if str(p.get("Provider", "")).upper() == "GITHUB" and str(p.get("BaseUrl", "")).rstrip("/") == GITHUB_BASE:
            return e
    return None


def cmd_git_provider(_):
    gh = read_env(".github-oauth")
    existing = github_provider()
    inp = {"provider": "GITHUB", "baseUrl": GITHUB_BASE,
           "clientId": gh["GITHUB_CLIENT_ID"], "clientSecret": gh["GITHUB_CLIENT_SECRET"]}
    if existing:
        inp["entityId"] = existing["entityId"]
    m = """mutation($inputs: [RepositoryScmProviderConfigInput!]!) { assessmentMutation { repositoryAssessmentMutation {
             createOrUpdateScmProviders(inputs: $inputs) { entity { entityId entityName } errors { errorMsg } } } } }"""
    r = gql(m, {"inputs": [inp]})["assessmentMutation"]["repositoryAssessmentMutation"]["createOrUpdateScmProviders"]
    errs = [e["errorMsg"] for x in r for e in (x.get("errors") or [])]
    if errs:
        die("Git provider: " + "; ".join(errs))
    print(f"GitHub OAuth app: {'updated' if existing else 'registered'} in Hub (provider {r[0]['entity']['entityId']})")


def git_connected():
    user = claims().get("email") or claims().get("user_name")
    q = """query($userId: Object!) { entityQuery { queryEntities(entityType: "Tanzu.Portfolio.ScmProvider", first: 50) {
             entities { entityId properties(name: ["Provider", "BaseUrl"]) { name value }
               connectionsIn: entitiesIn(entityType: "Tanzu.Portfolio.ScmConnection",
                 filter: { field: "properties.UserId", operator: EQ, values: [$userId] }) { entities { entityId } } } } } }"""
    for e in gql(q, {"userId": user})["entityQuery"]["queryEntities"]["entities"]:
        if str(props(e).get("Provider", "")).upper() == "GITHUB" and (e.get("connectionsIn") or {}).get("entities"):
            return True
    return False


def cmd_git_status(_):
    if not github_provider():
        print("GitHub: no OAuth app registered in Hub yet (run git-provider)")
    elif git_connected():
        print("GitHub: account connected")
    else:
        print("GitHub: OAuth app registered, account NOT connected yet.\n"
              f"  Open {HUB}/hub/configure/integrations/git and click Connect on the GitHub row.")
        sys.exit(2)


def cmd_checklist(args):
    if not args:
        die("checklist needs step names: " + ", ".join(STEPS))
    bad = [a for a in args if a not in STEPS]
    if bad:
        die(f"unknown step(s) {bad}; use: {', '.join(STEPS)}")
    q = 'query { userQuery { queryUserPrefs(name: ["onboardingFlowProgress"], scope: [USER]) { name value } } }'
    prefs = gql(q)["userQuery"]["queryUserPrefs"]
    state = json.loads(prefs[0]["value"]) if prefs and prefs[0].get("value") else {"version": 1, "flows": {}}
    flow = state.setdefault("flows", {}).setdefault(FLOW, {"status": "in-progress", "steps": {}})
    steps = flow.setdefault("steps", {})
    for a in args:
        steps[STEPS[a]] = "finished"
    flow["status"] = "finished" if all(steps.get(s) == "finished" for s in STEPS.values()) else "in-progress"
    m = """mutation($userPref: UserPrefInput!) { userMutation { upsertUserPref(input: $userPref) { name } } }"""
    gql(m, {"userPref": {"name": "onboardingFlowProgress", "scope": "USER", "value": json.dumps(state)}})
    done = [k for k, v in STEPS.items() if steps.get(v) == "finished"]
    print(f"Checklist (Set Up Spring Enterprise): done = {', '.join(done)}")


def group_by_name(name):
    q = """query($f: QueryFilter) { entityQuery { queryEntities(entityType: "Tanzu.Portfolio.RepositoryGroup", filter: $f, first: 50) {
             entities { entityId entityName } } } }"""
    f = {"field": "entityName", "operator": "EQ", "values": [name]}
    ents = gql(q, {"f": f})["entityQuery"]["queryEntities"]["entities"]
    return next((e for e in ents if e["entityName"] == name), None)


def cmd_repo(args):
    if not args:
        die("repo needs a GitHub URL, e.g. https://github.com/OWNER/REPO")
    url = args[0].removesuffix(".git")
    group = args[1] if len(args) > 1 else os.environ.get("HUB_REPO_GROUP", "My Spring Apps")
    private = os.environ.get("REPO_PUBLIC") != "1"
    if private and not git_connected():
        die("connect your GitHub account in Hub first (Configure > Integrations > Git > Connect), "
            "or set REPO_PUBLIC=1 for a public repo")
    repo = {"repositoryURL": url, "scmType": "GITHUB", "branch": os.environ.get("REPO_BRANCH", "main")}
    g = {"name": group, "description": "Added with hubctl.py",
         "repositoriesType": "REMOTE", "repositories": [repo], "runAssessment": True}
    if private:
        g["gitCreds"] = {"useOauthApp": True}
    existing = group_by_name(group)
    if existing:
        # Add (or update) just this repo in the group, keeping the group's other repos, then assess it.
        gid = existing["entityId"]
        if private:
            repo["gitCreds"] = {"useOauthApp": True}
        m = """mutation($r: [RepositoryInput!]!, $g: EntityId!) { assessmentMutation { repositoryAssessmentMutation {
                 createOrUpdateRepositories(repositories: $r, repositoryGroupEntityId: $g) { entity { entityId } error { errorMsg } } } } }"""
        r = gql(m, {"r": [repo], "g": gid})["assessmentMutation"]["repositoryAssessmentMutation"]["createOrUpdateRepositories"]
        errs = [x["error"]["errorMsg"] for x in r if x.get("error")]
        if errs:
            die("add repository: " + "; ".join(errs))
        rid = r[0]["entity"]["entityId"]
        m = """mutation($in: [RepositoryGroupOperationInput!]!) { assessmentMutation { repositoryAssessmentMutation {
                 runAssessments(repositoryAssessmentInputs: $in) { entityId } } } }"""
        gql(m, {"in": [{"repositoryGroupEntityId": gid, "repositories": [rid]}]})
        print(f"Repository group '{group}': added/updated {url}; assessment started")
        return
    m = """mutation($in: [RepositoryGroupInput!]!) { assessmentMutation { repositoryAssessmentMutation {
             createOrUpdateRepositoryGroups(repositoryGroupInputs: $in) { entity { entityId } errors { errorMsg } } } } }"""
    r = gql(m, {"in": [g]})["assessmentMutation"]["repositoryAssessmentMutation"]["createOrUpdateRepositoryGroups"]
    errs = [e["errorMsg"] for x in r for e in (x.get("errors") or [])]
    if errs:
        die("repository group: " + "; ".join(errs))
    print(f"Repository group '{group}': {'updated' if existing else 'created'} with {url}; assessment started")


def cmd_assessments(args):
    group = args[0] if args else os.environ.get("HUB_REPO_GROUP", "My Spring Apps")
    g = group_by_name(group)
    if not g:
        die(f"no repository group named '{group}'")
    q = """query($id: [EntityId!]) { entityQuery { queryEntities(entityId: $id) { entities { entityName
             latestTask: entitiesIn(entityType: "Tanzu.Hub.Task", sort: { field: "creationTime", order: DESC }, first: 1) {
               entities { entityName creationTime properties(name: "Status") { name value } } } } } } }"""
    e = gql(q, {"id": [g["entityId"]]})["entityQuery"]["queryEntities"]["entities"][0]
    tasks = e["latestTask"]["entities"]
    if not tasks:
        print(f"'{group}': no assessment task yet")
        return
    t = tasks[0]
    print(f"'{group}': latest task {t['entityName']} started {t['creationTime']} status {props(t).get('Status')}")


# ---------------------------------------------------------------- Secure Asset Catalog
# Hub fills the Secure Asset Catalog from Broadcom's "Product Warehouse" zip (~160 MB, ~690 MB unpacked).
# It has three sections, processed in this order: software-catalog, tiles-metadata (~600 MB of TAS tile
# metadata), library-catalog (~4 MB of Java libraries: what the Secure Asset Catalog shows). On this small
# cluster the tile section runs Hub services out of memory before the libraries are reached. These commands
# build a copy with only the sections you pick and load it the same way an airgapped install does.

def catalog_state():
    s = gql("{ warehouseQuery { state { running lastIndexStarted lastIndexFinished lastIndexResult } } }")
    # Same count the Secure Asset Catalog page uses (latest version of each library, by catalog type).
    q = """{ entityQuery { queryAggregations(entityQueryFilter: { entityType: ["Tanzu.Platform.ProductRelease"]
             filter: { and: [ { field: "properties.WarehouseSource", operator: EQ, values: ["LIBRARY_CATALOG"] }
                              { field: "properties.SemverOrder", operator: EQ, values: [1] }
                              { field: "properties.WarehouseDeletedAt", operator: ISNULL } ] } }
             aggregations: [{ fieldName: "properties.CatalogType" }]) { aggregations { buckets { value count } } } } }"""
    b = gql(q)["entityQuery"]["queryAggregations"]["aggregations"][0]["buckets"]
    n = sum(x["count"] for x in b)
    return s["warehouseQuery"]["state"], n


def cmd_catalog_status(_):
    st, n = catalog_state()
    print(f"Secure Asset Catalog: {n} libraries. Indexer running={st['running']} "
          f"started={st['lastIndexStarted']} finished={st['lastIndexFinished']} result={st['lastIndexResult']}")


def cmd_catalog_slim(args):
    """catalog-slim OUT.zip [SECTION ...]  (default section: library-catalog)"""
    import io, zipfile
    if not args:
        die("catalog-slim needs an output path, e.g. work/catalog-libraries.zip")
    out, keep = args[0], (args[1:] or ["library-catalog"])
    q = '{ documentQuery { queryDocuments(category: WAREHOUSE, first: 20) { documents { id name sizeBytes creationTime downloadLink { url } } } } }'
    docs = [d for d in gql(q)["documentQuery"]["queryDocuments"]["documents"] if d["name"] == "Product Warehouse v1"]
    if not docs:
        die("Hub has not downloaded the Broadcom catalog yet (run: python3 hubctl.py catalog-refresh)")
    doc = sorted(docs, key=lambda d: d["creationTime"])[-1]
    print(f"Downloading {doc['name']} ({doc['sizeBytes'] / 1e6:.0f} MB) from Hub ...")
    code, raw = http(doc["downloadLink"]["url"], headers={"Authorization": "Bearer " + token()})
    if code != 200:
        die(f"download failed: HTTP {code}")
    src = zipfile.ZipFile(io.BytesIO(raw))
    manifest = src.read("manifest.yaml").decode()
    lines = [l for l in manifest.splitlines()
             if not (l.strip().startswith("- ") and l.strip()[2:].strip('"') not in keep)]
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("manifest.yaml", "\n".join(lines) + "\n")
        n = 0
        for info in src.infolist():
            if info.filename.split("/")[0] in keep:
                z.writestr(info, src.read(info)); n += 1
    print(f"Wrote {out}: sections {keep}, {n} entries, {os.path.getsize(out) / 1e6:.1f} MB")


def cmd_catalog_load(args):
    """catalog-load ZIP  -- upload a warehouse zip and have Hub index it"""
    import uuid
    if not args:
        die("catalog-load needs a zip path")
    st, _ = catalog_state()
    if st["running"] and os.environ.get("FORCE") != "1":
        die("Hub's catalog indexer is still running; wait for it to finish (or FORCE=1)")
    path = args[0]
    # Link the upload to the same catalog release record as Hub's own download; the task's final step
    # fails ("Document.getEntity() is null") without it, even though the libraries are written.
    q = '{ documentQuery { queryDocuments(category: WAREHOUSE, first: 50) { documents { name creationTime entity { entityId } } } } }'
    docs = [d for d in gql(q)["documentQuery"]["queryDocuments"]["documents"]
            if d["name"] == "Product Warehouse v1" and d.get("entity")]
    release = sorted(docs, key=lambda d: d["creationTime"])[-1]["entity"]["entityId"] if docs else None
    q = """query($n:String!){ documentQuery { generateUploadHyperlink(category: WAREHOUSE, name: $n, type: ZIP) { url headers } } }"""
    link = gql(q, {"n": os.path.basename(path)})["documentQuery"]["generateUploadHyperlink"]
    hk, hv = link["headers"][0].split(":", 1)
    boundary = uuid.uuid4().hex
    data = open(path, "rb").read()
    fields = f"--{boundary}\r\nContent-Disposition: form-data; name=\"entityId\"\r\n\r\n{release}\r\n" if release else ""
    body = (fields + f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{os.path.basename(path)}\"\r\n"
            f"Content-Type: application/zip\r\n\r\n").encode() + data + f"\r\n--{boundary}--\r\n".encode()
    code, raw = http(link["url"], body, {hk.strip(): hv.strip(), "Content-Type": f"multipart/form-data; boundary={boundary}"})
    if code not in (200, 201):
        die(f"upload failed: HTTP {code} {raw[:200]!r}")
    doc_id = json.loads(raw)["documentId"]
    print(f"Uploaded {path} as document {doc_id}")
    m = """mutation($in: WarehouseProcessCollectionFromDocumentInput!) { warehouseMutation {
             processCollectionFromDocument(input: $in) { taskEntities { taskEntityId } } } }"""
    r = gql(m, {"in": {"documentId": doc_id}})["warehouseMutation"]["processCollectionFromDocument"]
    print("Indexing started:", ", ".join(t["taskEntityId"] for t in r["taskEntities"] or []))


def cmd_catalog_refresh(_):
    """Ask Hub to check Broadcom for a new catalog release (the UI's refresh button)."""
    r = gql("mutation { warehouseMutation { updateCollectionReleaseIfNeeded { taskEntities { taskEntityId } } } }")
    print("Started:", r["warehouseMutation"]["updateCollectionReleaseIfNeeded"]["taskEntities"])


def catalog_downloaded():
    q = '{ documentQuery { queryDocuments(category: WAREHOUSE, first: 20) { documents { name } } } }'
    return any(d["name"] == "Product Warehouse v1" for d in gql(q)["documentQuery"]["queryDocuments"]["documents"])


def cmd_catalog_fix(_):
    """Fill an empty Secure Asset Catalog. On a single Docker Desktop node, Hub's own index of the full Broadcom
    catalog can run a service out of memory and leave the index marked "running" with 0 libraries. This loads
    just the library section of Hub's download instead (about 7 MB, 5 minutes)."""
    from datetime import datetime, timezone
    st, n = catalog_state()
    if n > 0 and not st["running"]:
        print(f"Secure Asset Catalog: {n} libraries. Nothing to do.")
        return
    started = st.get("lastIndexStarted")
    age_min = None
    if started:
        age_min = (datetime.now(timezone.utc) - datetime.fromisoformat(started.replace("Z", "+00:00"))).total_seconds() / 60
    if st["running"] and (n > 0 or (age_min is not None and age_min < 20)):
        print(f"Secure Asset Catalog: {n} libraries, indexing since {started} ({age_min:.0f} min). "
              "Let it finish, then check again: python3 hubctl.py catalog-status")
        return
    if not catalog_downloaded():
        cmd_catalog_refresh([])
        print("Hub is downloading Broadcom's catalog (160 MB). Run this again in about 5 minutes.")
        return
    out = os.path.join(HERE, "work", "catalog-libraries.zip")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    cmd_catalog_slim([out])
    if st["running"]:
        print(f"Hub's own index has been 'running' for {age_min:.0f} minutes with {n} libraries (stuck); loading anyway.")
        os.environ["FORCE"] = "1"
    cmd_catalog_load([out])
    print("Check progress with: python3 hubctl.py catalog-status (about 1,900 libraries when done)")


# ---------------------------------------------------------------- Spring Insights (Application Advisor)
# Same as the "Analyze Spring Dependencies" onboarding step: a service account with the Spring Insights role
# (ensemble:app-advisor-connector). The Advisor CLI uses it to run `advisor build-config publish`.

def cmd_advisor_account(_):
    path = os.path.join(HERE, ".advisor-sa")
    if os.path.exists(path):
        print(f"Service account already set up ({path}); delete the file to create a new one")
        return
    # The UI reads the org ID from its env config (organization.id)
    code, raw = http(HUB + "/hub/assets/env-config/env-config.json")
    org_id = (json.loads(raw).get("organization") or {}).get("id") if code == 200 else None
    if not org_id:
        die("could not read the Hub org ID from /hub/assets/env-config/env-config.json")
    m = """mutation($input: [OAuthAppCreateInput]!) { authMutation { oAuthAppMutation { create(input: $input) {
             oauthAppId oauthAppSecret status { status errorMessage } } } } }"""
    app = gql(m, {"input": [{"name": "Spring Enterprise Service Account (demo)", "accessTokenTTLSecs": 31104000,
                             "description": "Application Advisor CLI: advisor build-config publish"}]})
    app = app["authMutation"]["oAuthAppMutation"]["create"][0]
    if not app.get("oauthAppId") or not app.get("oauthAppSecret"):
        die(f"service account not created: {app.get('status')}")
    m = """mutation($input: [AuthRoleCreateBindingInput!]!) { authMutation { authRoleMutation {
             authCreateRoleBindings(input: $input) { status { status errorMessage } } } } }"""
    rb = gql(m, {"input": [{"subjectId": app["oauthAppId"], "subjectType": "OAUTH_APP",
                            "roleName": "ensemble:app-advisor-connector"}]})
    st = rb["authMutation"]["authRoleMutation"]["authCreateRoleBindings"][0]["status"]
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.write(fd, (f"TANZU_PLATFORM_URL={HUB}\nTANZU_PLATFORM_OAUTH_APP_ID={app['oauthAppId']}\n"
                  f"TANZU_PLATFORM_OAUTH_APP_SECRET={app['oauthAppSecret']}\nTANZU_PLATFORM_ORG_ID={org_id}\n").encode())
    os.close(fd)
    print(f"Service account {app['oauthAppId']} created; Spring Insights role: {st['status']} {st.get('errorMessage') or ''}")
    print(f"Credentials written to {path} (mode 600)")


CMDS = {"advisor-account": cmd_advisor_account, "catalog-status": cmd_catalog_status, "catalog-slim": cmd_catalog_slim,
        "catalog-load": cmd_catalog_load, "catalog-refresh": cmd_catalog_refresh, "catalog-fix": cmd_catalog_fix,
        "login": cmd_login, "broadcom": cmd_broadcom, "git-provider": cmd_git_provider,
        "git-status": cmd_git_status, "checklist": cmd_checklist, "repo": cmd_repo, "assessments": cmd_assessments}

if __name__ == "__main__":
    if len(sys.argv) < 2 or sys.argv[1] not in CMDS:
        print(__doc__)
        sys.exit(1)
    CMDS[sys.argv[1]](sys.argv[2:])
