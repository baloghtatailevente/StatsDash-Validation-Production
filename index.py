from flask import Flask, request, jsonify, Response
import json
import requests
from flask_cors import CORS

app = Flask(__name__)
CORS(app)
SAVED_URLS = {}


@app.route("/", methods=["GET"])
def ui():
    # UI for inputting and saving URLs (page-only for entering endpoints)
    html = """
<!doctype html>
<html>
<head>
    <meta charset="utf-8" />
    <title>Validation Server — URL input</title>
    <style>
        body { font-family: Arial, Helvetica, sans-serif; max-width: 700px; margin: 40px auto; }
        label { display:block; margin-top: 12px; }
        input { width: 100%; padding: 8px; margin-top:4px; }
        button { margin-top: 12px; padding: 10px 16px; }
        pre { background:#f6f8fa; padding:12px; border-radius:6px; }
    </style>
</head>
<body>
    <h2>Validation Server — URL input</h2>
    <p>This page is only for entering the <strong>Users</strong> and <strong>Logs</strong> URLs that the validation server will use.
         Click <em>Save</em> to persist them. When you run the check, the server will read the saved URLs and use those endpoints.</p>

    <label>Players URL
        <input id="users_url" placeholder="https://example.com/players.json" />
    </label>
    <label>Logs URL
        <input id="logs_url" placeholder="https://example.com/points.json" />
    </label>

    <button id="saveBtn">Save</button>
    <button id="startBtn">Start check (uses saved URLs)</button>

    <h3>Result</h3>
    <pre id="result">Not started</pre>

    <script>
        // Load saved URLs on page load
        async function loadSaved(){
            try{
                const res = await fetch('/urls');
                if(res.ok){
                    const data = await res.json();
                    document.getElementById('users_url').value = data.users_url || '';
                    document.getElementById('logs_url').value = data.logs_url || '';
                }
            }catch(e){
                console.warn('Failed to load saved URLs', e);
            }
        }

        // Save current inputs to server
        async function saveUrls(){
            const users_url = document.getElementById('users_url').value.trim();
            const logs_url = document.getElementById('logs_url').value.trim();
            const payload = { users_url, logs_url };
            try{
                const res = await fetch('/urls', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify(payload)
                });
                const data = await res.json();
                document.getElementById('result').textContent = data.message || JSON.stringify(data);
            }catch(err){
                document.getElementById('result').textContent = 'Save error: ' + err.toString();
            }
        }

        // Start check — do not send URLs; server will read saved URLs
        async function startCheck(){
            document.getElementById('result').textContent = 'Running...';
            try{
                const res = await fetch('/start');
                const data = await res.json();
                document.getElementById('result').textContent = JSON.stringify(data, null, 2);
            }catch(err){
                document.getElementById('result').textContent = 'Error: ' + err.toString();
            }
        }

        document.getElementById('saveBtn').addEventListener('click', saveUrls);
        document.getElementById('startBtn').addEventListener('click', startCheck);
        // Load saved on open
        loadSaved();
    </script>
</body>
</html>
    """
    return Response(html, mimetype="text/html")


@app.route("/urls", methods=["GET", "POST"])
def urls_endpoint():
    if request.method == "GET":
        return jsonify(SAVED_URLS)

    if request.method == "POST":
        if not request.is_json:
            return jsonify({"error": "expected JSON body"}), 400

        body = request.get_json() or {}
        if "users_url" in body:
            SAVED_URLS["users_url"] = body["users_url"]
        if "logs_url" in body:
            SAVED_URLS["logs_url"] = body["logs_url"]

        return jsonify({"message": "urls saved", "urls": SAVED_URLS})


@app.route("/start", methods=["GET", "POST"])
def check_points():
    # Accept both POST (with JSON body) and GET (with query params) to start the check.
    body = {}
    # GET: allow /start?users_url=...&logs_url=...
    if request.method == 'GET':
        users_url = request.args.get('users_url')
        logs_url = request.args.get('logs_url')
        if users_url:
            body['users_url'] = users_url
        if logs_url:
            body['logs_url'] = logs_url
    else:
        # POST
        if request.is_json:
            try:
                body = request.get_json()
            except Exception:
                body = {}

    # Always prefer saved URLs as the base for checks; request body can override saved values.
    saved_urls = SAVED_URLS.copy()

    merged = {}
    merged.update(saved_urls or {})
    merged.update(body or {})
    body = merged

    users_data = None
    points_logs_data = None

    # Helper to fetch JSON from a URL with a short timeout
    def fetch_json(url):
        try:
            r = requests.get(url, timeout=5)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            raise RuntimeError(f"Failed to fetch {url}: {e}")
    try:
        if body.get('users_url'):
            data = fetch_json(body['users_url'])
            # Accept either {"users": [...]} or a top-level array [...]
            if isinstance(data, dict):
                users_data = data.get('users') or data.get('data')
                # Single user object -> wrap in list
                if users_data is None and any(k in data for k in ("_id", "id", "name")):
                    users_data = [data]
            elif isinstance(data, list):
                users_data = data
            if users_data is None:
                raise RuntimeError('users_url did not return expected JSON (array or object with "users" key)')

        if body.get('logs_url'):
            data = fetch_json(body['logs_url'])
            # Accept either {"points_logs": [...]} or a top-level array [...]
            if isinstance(data, dict):
                points_logs_data = data.get('points_logs') or data.get('logs') or data.get('data')
                # Single log object -> wrap in list
                if points_logs_data is None and any(k in data for k in ("user", "user_id", "points")):
                    points_logs_data = [data]
            elif isinstance(data, list):
                points_logs_data = data
            if points_logs_data is None:
                raise RuntimeError('logs_url did not return expected JSON (array or object with "points_logs" key)')

        # If any of the datasets are still None, try loading local files
        if users_data is None or points_logs_data is None:
            # Load users data from local files
            try:
                with open("ValidationServer/users.json", "r", encoding="utf-8") as f:
                    local_users = json.load(f).get('users')
                with open("ValidationServer/points.json", "r", encoding="utf-8") as f:
                    local_logs = json.load(f).get('points_logs')
            except Exception as e:
                return jsonify({"error": f"Failed to load JSON files: {str(e)}"}), 500

            if users_data is None:
                users_data = local_users
            if points_logs_data is None:
                points_logs_data = local_logs

    except RuntimeError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        return jsonify({"error": f"Unexpected error: {str(e)}"}), 500

    # Sum points per user from logs
    points_sum = {}
    for log in points_logs_data:
        # Accept different key names for the user reference in logs
        user_id = log.get("user") or log.get("user_id") or log.get("userId") or log.get("userid")
        points = log.get("points", 0)
        if user_id is None:
            continue
        points_sum[user_id] = points_sum.get(user_id, 0) + points

    # Check for mismatches
    mismatches = []
    for user in users_data:
        user_id = user.get("_id") or user.get("id")
        total_points = points_sum.get(user_id, 0)
        user_points = user.get("points", 0)
        if total_points != user_points:
            # Normalize name fields
            firstname = user.get("firstname")
            lastname = user.get("lastname")
            name = user.get("name")
            if not firstname and name:
                parts = name.split()
                if parts:
                    firstname = parts[0]
                    lastname = " ".join(parts[1:]) if len(parts) > 1 else None

            mismatches.append({
                "id": user_id,
                "name": name or (f"{firstname} {lastname}".strip() if firstname or lastname else None),
                "user_points": user_points,
                "sum_of_logs": total_points
            })

    if mismatches:
        return jsonify({"mismatches": mismatches}), 417
    else:
        return jsonify({"message": "All users' points match their logs."}), 200


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=True)
