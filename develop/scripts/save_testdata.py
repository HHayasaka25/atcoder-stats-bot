import csv
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

# ==========================
# 設定
# ==========================

USER_ID_LIST = ["LIama", "Dona_", "kotatsugame"]  # 自分のAtCoder IDに変更
BASE_URL = "https://kenkoooo.com/atcoder"
JST = ZoneInfo("Asia/Tokyo")
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "testdata"

session = requests.Session()


def get_json(url, params=None):
    for attempt in range(5):
        try:
            response = session.get(
                url, params=params, timeout=60
            )
            response.raise_for_status()
            return response.json()

        except requests.RequestException:
            if attempt == 4:
                raise
            time.sleep(3 * (attempt + 1))


# ==========================
# 全提出履歴の取得
# ==========================

def get_all_submissions(user_id):
    submissions = {}
    from_second = 0

    while True:
        url = (
            f"{BASE_URL}/atcoder-api/v3/"
            "user/submissions"
        )
        data = get_json(
            url,
            params={
                "user": user_id,
                "from_second": from_second,
            },
        )

        if not isinstance(data, list):
            raise ValueError("APIが配列を返しませんでした")

        for submission in data:
            submissions[submission["id"]] = submission

        print(f"取得済み: {len(submissions)} 件")

        if len(data) < 500:
            break

        # 同じ秒の提出を取りこぼす可能性があるため、
        # 境界秒を重複取得してIDで重複を除去する。
        last_second = max(
            s["epoch_second"] for s in data
        )

        if last_second <= from_second:
            raise RuntimeError(
                "同一秒の提出が500件以上あるため、"
                "安全にページ送りできません"
            )

        from_second = last_second
        time.sleep(1.2)

    return sorted(
        submissions.values(),
        key=lambda s: (s["epoch_second"], s["id"]),
    )


# ==========================
# 問題情報・Difficulty取得
# ==========================

def get_problem_data():
    problems = get_json(
        f"{BASE_URL}/resources/merged-problems.json"
    )
    time.sleep(1.2)

    models = get_json(
        f"{BASE_URL}/resources/problem-models.json"
    )

    problem_map = {
        p["id"]: p for p in problems
    }

    return problem_map, models


# ==========================
# CSV出力
# ==========================

def save_csv(filename, rows, fields):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    filename = OUTPUT_DIR / filename
    with open(
        filename, "w", encoding="utf-8-sig", newline=""
    ) as f:
        writer = csv.DictWriter(
            f, fieldnames=fields, extrasaction="ignore"
        )
        writer.writeheader()
        writer.writerows(rows)

    print(f"保存完了: {filename}")


def to_jst(epoch_second):
    return datetime.fromtimestamp(
        epoch_second, JST
    ).strftime("%Y-%m-%d %H:%M:%S")


def main():
    for USER_ID in USER_ID_LIST:
        submissions = get_all_submissions(USER_ID)

        # 提出履歴の元データの項目
        raw_fields = [
            "id", "epoch_second", "problem_id",
            "contest_id", "user_id", "language",
            "point", "length", "result",
            "execution_time",
        ]

        # 問題情報を取得
        time.sleep(1.2)
        problem_map, models = get_problem_data()

        enriched = []

        for s in submissions:
            pid = s["problem_id"]
            problem = problem_map.get(pid, {})
            model = models.get(pid, {})

            row = {
                **s,
                "datetime_jst": to_jst(s["epoch_second"]),
                "problem_name": problem.get("name"),
                "difficulty": (
                    model.get("difficulty")
                    if isinstance(model, dict) else None
                ),
                "problem_url": (
                    f"https://atcoder.jp/contests/"
                    f"{s['contest_id']}/tasks/{pid}"
                ),
            }

            enriched.append(row)

        fields = raw_fields + [
            "datetime_jst",
            "problem_name",
            "difficulty",
            "problem_url",
        ]

        # 問題名・Difficulty付きの全提出履歴
        save_csv(
            f"{USER_ID}_submissions_full.csv",
            enriched,
            fields,
        )

        print(f"\n全提出数: {len(submissions)}")


if __name__ == "__main__":
    main()
