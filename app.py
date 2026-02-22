# app.py
from __future__ import annotations

from pathlib import Path
import io
import re
import sqlite3
from typing import Dict, List

import pandas as pd
from flask import (
    Flask,
    jsonify,
    request,
)

# =============================
# パス / Flask アプリ設定
# =============================

# app.py が置いてあるディレクトリ（= surgery-cost プロジェクト直下）
BASE_DIR = Path(__file__).resolve().parent
DB_PATH = BASE_DIR / "surgDB.db"

# 静的ファイルとして index.html / app.js / styles.css 等を配信する
#   static_folder   : 静的ファイルの実体があるパス
#   static_url_path : URL プレフィックス。"" にすると /index.html で直接取れる
app = Flask(__name__, static_folder=str(BASE_DIR), static_url_path="")


# =============================
# ユーティリティ
# =============================

def get_db_connection() -> sqlite3.Connection:
    """SQLite 接続を取得する。毎リクエストごとに新規接続。"""
    conn = sqlite3.connect(DB_PATH)
    # 日本語カラム名などもあるので、一応 TEXT は str で扱えるようデフォルトのまま
    return conn


def normalize_header(raw: str) -> str:
    """
    CSV のヘッダー行の文字列を正規化して、共通のキーにする。

    - 前後スペース削除
    - 全角スペース → 半角スペース
    - 全角カッコ → 半角カッコ
    - 連続スペース圧縮
    """
    if raw is None:
        return ""

    s = str(raw).strip()
    s = s.replace("　", " ")          # 全角スペース
    s = s.replace("（", "(").replace("）", ")")  # 全角カッコ
    s = re.sub(r"\s+", " ", s)        # 連続スペース → 1 個
    return s


# 画面に記載されている「必要な列（見出し）」と DB カラムの対応表
HEADER_MAP: Dict[str, str] = {
    # 日本語ヘッダー                # DB カラム名
    "症例ID": "case_id",
    "患者番号": "patient_id",
    "患者氏名(漢字)": "patient_name",
    "患者氏名（漢字）": "patient_name",  # 念のため全角カッコも直接マッピング
    "手術実施日": "surg_date",
    "年齢": "age",
    "実施診療科": "dept",
    "確定術式フリー検索": "surg_procedure",
    "術後病名": "disease",
}

# surg_cases テーブルとして最低限必要なカラム
REQUIRED_DB_COLUMNS: List[str] = [
    "case_id",
    "patient_id",
]


def parse_int_safe(val):
    """
    年齢などの数値をゆるくパースするヘルパ。
    - NaN / 空文字 → None
    - '10歳' / '約20' / '30 才' などから先頭の数字だけを抜き出して int にする
    """
    if pd.isna(val):
        return None
    s = str(val).strip()
    if not s:
        return None
    m = re.search(r"\d+", s)
    return int(m.group()) if m else None


def read_csv_to_dataframe(file_storage) -> pd.DataFrame:
    """
    Flask の FileStorage を pandas DataFrame に変換する。
    文字コードは CP932 → UTF-8-SIG → UTF-8 の順でトライする。
    """
    content = file_storage.read()
    if not content:
        raise ValueError("ファイルが空です。")

    last_error = None
    for enc in ("cp932", "utf-8-sig", "utf-8"):
        try:
            text = content.decode(enc)
            buf = io.StringIO(text)
            df = pd.read_csv(buf)
            return df
        except UnicodeDecodeError as e:
            last_error = e

    raise ValueError(f"CSV の文字コードを判別できませんでした: {last_error}")


def normalize_dataframe_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    DataFrame の列名を normalize_header で正規化し、
    HEADER_MAP をもとに DB カラム名に rename する。
    """
    # 1) normalize_header を当てる
    normalized_to_original: Dict[str, str] = {}
    new_columns = {}
    for original in df.columns:
        norm = normalize_header(original)
        normalized_to_original[norm] = original
        new_columns[original] = norm
    df = df.rename(columns=new_columns)

    # 2) 日本語ヘッダー → DB カラム名 に変換
    rename_to_db = {}
    for norm_name in df.columns:
        if norm_name in HEADER_MAP:
            rename_to_db[norm_name] = HEADER_MAP[norm_name]
    df = df.rename(columns=rename_to_db)

    return df


def validate_required_columns(df: pd.DataFrame):
    """必須カラムが揃っているかをチェック。足りなければ ValueError を投げる。"""
    missing = [col for col in REQUIRED_DB_COLUMNS if col not in df.columns]
    if missing:
        jp_names = []
        # 足りないカラムの日本語名を逆引き（あれば）
        for db_col in missing:
            jp = None
            for jp_name, db_name in HEADER_MAP.items():
                if db_name == db_col:
                    jp = jp_name
                    break
            jp_names.append(jp or db_col)

        raise ValueError(
            "必須列が不足しています: " + ", ".join(jp_names) +
            "（ヘッダー行や文字列の揺れを確認してください）"
        )


# =============================
# ルーティング
# =============================

@app.route("/")
def index():
    """トップページとして index.html を返す。"""
    return app.send_static_file("index.html")


@app.post("/api/import-csv")
def import_csv():
    """
    CSV を受け取り、surg_cases / case_usage テーブルに登録するエンドポイント。

    フロントからは multipart/form-data で "file" キーのファイルが送られてくる想定。
    レスポンス形式（app.js に合わせる）:
    {
      "ok": true/false,
      "message": "インポート完了メッセージ",
      "imported_cases":  数値,
      "imported_usage_rows": 数値
    }
    """
    upload = request.files.get("file")
    if upload is None or upload.filename == "":
        return jsonify({"ok": False, "error": "CSVファイルが選択されていません。"}), 400

    # 拡張子チェック（ゆるめ）
    if not upload.filename.lower().endswith(".csv"):
        return jsonify({"ok": False, "error": "CSVファイル（.csv）を指定してください。"}), 400

    try:
        # pandas で CSV を読み取り
        df = read_csv_to_dataframe(upload)
        if df.empty:
            return jsonify({"ok": False, "error": "CSVにデータ行がありません。"}), 400

        # 列名を正規化 → DB カラム名に rename
        df = normalize_dataframe_columns(df)

        # 必須カラムチェック
        validate_required_columns(df)

        # surg_cases に投入するデータフレームを作成
        # 無い列は None に埋める
        for col in [
            "case_id",
            "patient_id",
            "patient_name",
            "surg_date",
            "age",
            "dept",
            "surg_procedure",
            "disease",
        ]:
            if col not in df.columns:
                df[col] = None

        # 年齢カラムだけは int に変換
        df["age"] = df["age"].map(parse_int_safe)

        # DB 書き込み
        imported_cases = 0
        imported_usage_rows = 0  # 物品側は今後拡張する前提で 0 にしておく

        with get_db_connection() as conn:
            cur = conn.cursor()

            # surg_cases を upsert（case_id が被ったら上書き）
            records = df[
                [
                    "case_id",
                    "patient_id",
                    "patient_name",
                    "surg_date",
                    "age",
                    "dept",
                    "surg_procedure",
                    "disease",
                ]
            ].itertuples(index=False, name=None)

            cur.executemany(
                """
                INSERT INTO surg_cases (
                  case_id,
                  patient_id,
                  patient_name,
                  surg_date,
                  age,
                  dept,
                  surg_procedure,
                  disease
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(case_id) DO UPDATE SET
                  patient_id      = excluded.patient_id,
                  patient_name    = excluded.patient_name,
                  surg_date       = excluded.surg_date,
                  age             = excluded.age,
                  dept            = excluded.dept,
                  surg_procedure  = excluded.surg_procedure,
                  disease         = excluded.disease
                """,
                list(records),
            )

            imported_cases = cur.rowcount  # SQLite では厳密ではないが目安値

            conn.commit()

        message = f"インポートが完了しました。（症例 {imported_cases} 件）"
        return jsonify(
            {
                "ok": True,
                "message": message,
                "imported_cases": int(imported_cases or 0),
                "imported_usage_rows": int(imported_usage_rows or 0),
            }
        )

    except ValueError as e:
        # 想定される入力ミスなど
        return jsonify({"ok": False, "error": str(e)}), 400
    except sqlite3.Error as e:
        return jsonify({"ok": False, "error": f"データベースエラー: {e}"}), 500
    except Exception as e:
        # 予期しない例外
        return jsonify({"ok": False, "error": f"インポート処理でエラーが発生しました: {e}"}), 500

@app.get("/api/cases")
def list_cases():
    """
    surg_cases テーブルの一覧を返す API。
    cases.html から Fetch で呼び出す想定。
    レスポンス例:
    {
      "ok": true,
      "cases": [
        {
          "case_id": "...",
          "patient_id": "...",
          "patient_name": "...",
          "surg_date": "2025-01-01",
          "age": 70,
          "dept": "外科",
          "disease": "〜",
          "surg_procedure": "〜"
        },
        ...
      ]
    }
    """
    try:
        with get_db_connection() as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute(
                """
                SELECT
                  case_id,
                  patient_id,
                  patient_name,
                  surg_date,
                  age,
                  dept,
                  disease,
                  surg_procedure
                FROM surg_cases
                ORDER BY
                  COALESCE(surg_date, ''),
                  case_id
                """
            )
            rows = cur.fetchall()

        cases = [dict(row) for row in rows]
        return jsonify({"ok": True, "cases": cases})
    except sqlite3.Error as e:
        return (
            jsonify({"ok": False, "error": f"データベースエラー: {e}"}),
            500,
        )
    
@app.get("/api/health")
def health():
    """簡易ヘルスチェック（開発・デバッグ用）。"""
    return jsonify({"ok": True, "db_path": str(DB_PATH)})


if __name__ == "__main__":
    # 開発中は debug=True でホットリロード
    app.run(debug=True)