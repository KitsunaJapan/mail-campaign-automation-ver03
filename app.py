from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import anthropic
import requests
import json
import os
import re
from datetime import date, timedelta

app = Flask(__name__)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CORS(app, resources={r"/*": {"origins": "*"}})

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
SPREADSHEET_ID    = os.environ.get("SPREADSHEET_ID", "10gH3TlsQOtgPnDW1AhHErpxNsBXv5kgudGJuHms5jyE")


def parse_period_to_weeks(period_str):
    if not period_str:
        return 4
    p = period_str.replace(' ', '').replace('　', '')
    m = re.search(r'(\d+(?:\.\d+)?)\s*年', p)
    if m: return int(float(m.group(1)) * 52)
    m = re.search(r'(\d+(?:\.\d+)?)\s*[ヶカか]月', p)
    if m: return max(1, int(float(m.group(1)) * 4.3))
    m = re.search(r'(\d+)\s*週', p)
    if m: return int(m.group(1))
    m = re.search(r'(\d+)\s*日', p)
    if m: return max(1, int(m.group(1)) // 7)
    if re.search(r'Q[1-4]', p, re.IGNORECASE): return 13
    if '半期' in p: return 26
    return 4


def generate_week_dates(num_weeks):
    today  = date.today()
    monday = today - timedelta(days=today.weekday())
    weeks  = []
    for w in range(num_weeks):
        week_start = monday + timedelta(weeks=w)
        week_days  = [(week_start + timedelta(days=d)).isoformat() for d in range(7)]
        weeks.append(week_days)
    return weeks


@app.route("/")
def index():
    return send_from_directory(BASE_DIR, "index.html")


@app.route("/api/config")
def api_config():
    return jsonify({
        "hasAnthropicKey": bool(ANTHROPIC_API_KEY),
        "googleClientId":  os.environ.get("GOOGLE_CLIENT_ID", ""),
        "spreadsheetId":   SPREADSHEET_ID
    })


@app.route("/api/generate_tasks", methods=["POST"])
def generate_tasks():
    if not ANTHROPIC_API_KEY:
        return jsonify({"error": "ANTHROPIC_API_KEY が未設定です"}), 503

    data = request.json or {}
    goal = data.get("goal", {})
    if not goal.get("content"):
        return jsonify({"error": "目標内容が必要です"}), 400

    pmap      = {"asap": "ASAP", "high": "高", "mid": "中", "low": "低"}
    period    = goal.get("period", "")
    num_weeks = min(parse_period_to_weeks(period), 26)
    all_weeks = generate_week_dates(num_weeks)

    week_summaries = []
    for i, week in enumerate(all_weeks):
        week_summaries.append(f"第{i+1}週: {week[0]} 〜 {week[6]}")

    prompt = f"""あなたはタスク管理の専門家です。以下の中長期目標を達成するために、期間全体にわたる週次タスクを作成してください。

目標: {goal.get('content', '')}
達成期間: {period}（{num_weeks}週間）
担当者: {goal.get('owner', '未設定')}
優先度: {pmap.get(goal.get('priority', ''), goal.get('priority', ''))}
備考: {goal.get('note', 'なし')}

対象期間:
{chr(10).join(week_summaries)}

要件:
- 各週に2〜5件のタスクを作成してください
- 前半は基盤づくり・調査・計画、後半は実行・改善・仕上げというように段階的に設定してください
- 各タスクは具体的なアクションとして記述してください

以下のJSON配列のみを返してください（説明文・マークダウン不要）:
[{{"date":"YYYY-MM-DD","time":"09-12","name":"タスク名30字以内","detail":"詳細80字以内","priority":"asap|high|mid|low"}}]

dateは各週のいずれかの日付（YYYY-MM-DD形式）を使用してください。"""

    try:
        client  = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        max_tok = min(4096, 300 * num_weeks + 500)
        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=max_tok,
            messages=[{"role": "user", "content": prompt}]
        )
        text  = message.content[0].text.strip()
        match = re.search(r'\[[\s\S]*\]', text)
        if not match:
            raise ValueError("JSONが見つかりません: " + text[:200])
        tasks = json.loads(match.group())
        return jsonify({"tasks": tasks, "num_weeks": num_weeks, "total_tasks": len(tasks)})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/sheets/get", methods=["POST"])
def sheets_get():
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
