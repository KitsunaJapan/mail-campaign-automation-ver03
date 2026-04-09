from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import anthropic
import requests
import json
import os
import re

app = Flask(__name__)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CORS(app, resources={r"/*": {"origins": "*"}})

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
APP_PASSWORD      = os.environ.get("APP_PASSWORD", "")
SPREADSHEET_ID    = os.environ.get("SPREADSHEET_ID", "10gH3TlsQOtgPnDW1AhHErpxNsBXv5kgudGJuHms5jyE")


def check_auth():
    if not APP_PASSWORD:
        return True
    return request.headers.get("X-App-Password", "") == APP_PASSWORD


@app.route("/")
def index():
    return send_from_directory(BASE_DIR, "index.html")


@app.route("/login", methods=["POST"])
def login():
    data = request.json or {}
    if not APP_PASSWORD or data.get("password") == APP_PASSWORD:
        return jsonify({"success": True})
    return jsonify({"error": "パスワードが違います"}), 401


@app.route("/api/config")
def api_config():
    return jsonify({
        "hasAnthropicKey": bool(ANTHROPIC_API_KEY),
        "googleClientId": os.environ.get("GOOGLE_CLIENT_ID", ""),
        "spreadsheetId": SPREADSHEET_ID
    })


@app.route("/api/generate_tasks", methods=["POST"])
def generate_tasks():
    if not check_auth():
        return jsonify({"error": "認証が必要です"}), 401
    if not ANTHROPIC_API_KEY:
        return jsonify({"error": "ANTHROPIC_API_KEY が未設定です"}), 503

    data = request.json or {}
    goal = data.get("goal", {})
    if not goal.get("content"):
        return jsonify({"error": "目標内容が必要です"}), 400

    from datetime import date, timedelta
    today  = date.today()
    monday = today - timedelta(days=today.weekday())
    days   = [(monday + timedelta(days=i)).isoformat() for i in range(7)]
    pmap   = {"asap": "ASAP", "high": "高", "mid": "中", "low": "低"}

    prompt = f"""あなたはタスク管理の専門家です。以下の中長期目標から今週の具体的な業務タスクを作成してください。

目標: {goal.get('content', '')}
達成期間: {goal.get('period', '未設定')}
担当者: {goal.get('owner', '未設定')}
優先度: {pmap.get(goal.get('priority', ''), goal.get('priority', ''))}
備考: {goal.get('note', 'なし')}
今週の日付: {', '.join(days)}

以下のJSON配列のみを返してください（説明文・マークダウン不要）:
[{{"date":"YYYY-MM-DD","time":"09-12","name":"タスク名30字以内","detail":"詳細80字以内","priority":"asap|high|mid|low"}}]

今週全体で6〜10件、日付を分散させて作成してください。"""

    try:
        client  = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        message = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=1500,
            messages=[{"role": "user", "content": prompt}]
        )
        text  = message.content[0].text.strip()
        match = re.search(r'\[[\s\S]*\]', text)
        if not match:
            raise ValueError("JSONが見つかりません")
        tasks = json.loads(match.group())
        return jsonify({"tasks": tasks})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/sheets/get", methods=["POST"])
def sheets_get():
    if not check_auth():
        return jsonify({"error": "認証が必要です"}), 401
    data   = request.json or {}
    token  = data.get("token")
    range_ = data.get("range")
    if not token or not range_:
        return jsonify({"error": "token と range が必要です"}), 400
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{SPREADSHEET_ID}/values/{requests.utils.quote(range_, safe='')}"
    res = requests.get(url, headers={"Authorization": f"Bearer {token}"})
    if not res.ok:
        err = res.json().get("error", {})
        return jsonify({"error": err.get("message", "Sheets GETエラー")}), res.status_code
    return jsonify(res.json())


@app.route("/api/sheets/batch_update", methods=["POST"])
def sheets_batch_update():
    if not check_auth():
        return jsonify({"error": "認証が必要です"}), 401
    data    = request.json or {}
    token   = data.get("token")
    updates = data.get("data", [])
    if not token or not updates:
        return jsonify({"error": "token と data が必要です"}), 400
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{SPREADSHEET_ID}/values:batchUpdate"
    res = requests.post(url,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"valueInputOption": "USER_ENTERED", "data": updates}
    )
    if not res.ok:
        err = res.json().get("error", {})
        return jsonify({"error": err.get("message", "Sheets batchUpdateエラー")}), res.status_code
    return jsonify(res.json())


@app.route("/api/sheets/put", methods=["POST"])
def sheets_put():
    if not check_auth():
        return jsonify({"error": "認証が必要です"}), 401
    data   = request.json or {}
    token  = data.get("token")
    range_ = data.get("range")
    values = data.get("values", [])
    if not token or not range_:
        return jsonify({"error": "token と range が必要です"}), 400
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{SPREADSHEET_ID}/values/{requests.utils.quote(range_, safe='')}?valueInputOption=USER_ENTERED"
    res = requests.put(url,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"range": range_, "majorDimension": "ROWS", "values": values}
    )
    if not res.ok:
        err = res.json().get("error", {})
        return jsonify({"error": err.get("message", "Sheets PUTエラー")}), res.status_code
    return jsonify(res.json())


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
