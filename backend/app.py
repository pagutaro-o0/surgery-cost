from __future__ import annotations

from pathlib import Path
import io
import re
import sqlite3
from typing import Any

import pandas as pd
from flask import Flask, jsonify, request, send_from_directory


# -----------------------------
# Path / App
# -----------------------------
BASE_DIR = Path(__file__).resolve().parent.parent  # surgery-cost/
DB_PATH = BASE_DIR / "surgDB.db"

app = Flask(__name__, static_folder=str(BASE_DIR), static_url_path="")


# -----------------------------
# Static pages
# -----------------------------
@app.get("/")
def index():
    return send_from_directory(BASE_DIR, "index.html")


@app.get("/<path:filename>")
def static_files(filename: str):
    # index.html / cases.html / case-usages.html / app.js / styles.css など全部ここで配信
    return send_from_directory(BASE_DIR, filename)


# -----------------------------
# DB helpers
# -----------------------------
def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_schema() -> None:
    """
    既存DBを壊さずに、CSVの「症例ID」を保持する ext_case_id を surg_cases に追加する。
    - surg_cases.case_id は内部ID（INTEGER PK AUTOINCREMENT）
    - surg_cases.ext_case_id は外部ID（CSVの症例ID）
    """
    conn = get_conn()
    try:
        cur = conn.cursor()

        # テーブル存在チェック
        cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='surg_cases'")
        if cur.fetchone() is None:
            raise RuntimeError("DBに surg_cases テーブルが見つかりません（surgDB.db を確認してください）")

        # 列チェック
        cur.execute("PRAGMA table_info(surg_cases)")
        cols = [r["name"] for r in cur.fetchall()]

        if "ext_case_id" not in cols:
            cur.execute("ALTER TABLE surg_cases ADD COLUMN ext_case_id TEXT")
            # UPSERT用に一意制約相当のIndexを追加（NULLは重複OK）
            cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_surg_cases_ext_case_id ON surg_cases(ext_case_id)")

        conn.commit()
    finally:
        conn.close()


@app.before_request
def _ensure_schema_once():
    # 初回アクセス時にschemaを整える（本番なら起動時1回でもOK）
    ensure_schema()


# -----------------------------
# CSV column requirements
# -----------------------------
REQUIRED_COLUMNS = [
    "症例ID",
    "患者番号",
    "患者氏名(漢字)",
    "年齢",
    "手術実施日",
    "実施診療科",
    "確定術式フリー検索",
    "術後病名",
    "リマークス（看護）",
]


def normalize_headers(df: pd.DataFrame) -> pd.DataFrame:
    # 全角空白→半角、前後空白除去
    df.columns = [str(c).replace("\u3000", " ").strip() for c in df.columns]
    return df


def validate_headers(df: pd.DataFrame) -> None:
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError("必須列が不足しています: " + ", ".join(missing))


def to_iso_date(val: Any) -> str | None:
    if pd.isna(val):
        return None
    s = str(val).strip()
    if not s:
        return None
    dt = pd.to_datetime(s, errors="coerce")
    if pd.isna(dt):
        raise ValueError(f"手術実施日の形式が不正です: {s}")
    return dt.strftime("%Y-%m-%d")


def parse_int_safe(val: Any) -> int | None:
    if pd.isna(val):
        return None
    s = str(val).strip()
    if s == "":
        return None
    m = re.search(r"\d+", s)
    return int(m.group()) if m else None


# -----------------------------
# Build surg_cases rows
# -----------------------------
def build_surg_cases(df: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame()
    out["ext_case_id"] = df["症例ID"].astype(str).str.strip()
    out["patient_id"] = df["患者番号"].apply(parse_int_safe)
    out["patient_name"] = df["患者氏名(漢字)"].astype(str).str.strip()
    out["surg_date"] = df["手術実施日"].apply(to_iso_date)
    out["age"] = df["年齢"].apply(parse_int_safe)
    out["dept"] = df["実施診療科"].astype(str).str.strip()
    out["surg_procedure"] = df["確定術式フリー検索"].astype(str).str.strip()
    out["disease"] = df["術後病名"].astype(str).str.strip()
    out["remarks"] = df["リマークス（看護）"].astype(str).fillna("").str.strip()

    # 空のext_case_id除外
    out = out[out["ext_case_id"] != ""].copy()

    # 同一ext_case_idが複数あれば先頭採用
    out = out.drop_duplicates(subset=["ext_case_id"], keep="first")
    return out


# -----------------------------
# Build case_usage rows (extract from remarks)
# -----------------------------
def parse_usage_from_remarks(internal_case_id: int, remarks: Any):
    """
    例:
      ★サージセル[2]枚
      ★洗浄[生理食塩水250ml][1]本
      ★クリップ[3]個

    を抽出して case_usage 行にする
    """
    results = []
    if remarks is None or (isinstance(remarks, float) and pd.isna(remarks)):
        return results

    text = str(remarks)

    # 半角/全角カンマ/読点区切り
    parts = re.split(r"[,\u3001，]", text)

    for p in parts:
        p = p.strip()
        if not p.startswith("★"):
            continue

        memo = p

        # 末尾の [数値] + 単位 を quantity/unit として取得
        # 例: ★洗浄[生理食塩水250ml][1]本
        m = re.match(r"^★\s*(.*?)(?:\[(\d+(?:\.\d+)?)\])\s*([^\]]*)\s*$", p)
        if m:
            left = (m.group(1) or "").strip()
            qty_str = m.group(2)
            unit = (m.group(3) or "").strip() or None

            # free_item_name は最初の [ の前
            item_name = left.split("[")[0].strip()

            # DBはINTEGER想定なので、少数は四捨五入ではなく「文字→float→int」にせずそのまま保持したい場合はTEXTにする必要あり
            # 今回は安全に「数値っぽいなら int / float を許す」が、INSERT時に str で入れる（SQLiteは許容）
            quantity: Any = float(qty_str) if "." in qty_str else int(qty_str)

            if item_name:
                results.append(
                    {
                        "case_id": internal_case_id,
                        "free_item_name": item_name,
                        "quantity": quantity,
                        "unit": unit,
                        "memo": memo,
                    }
                )
            continue

        # フォールバック：数量が取れないが品目名だけは取る
        m2 = re.match(r"^★\s*(.*?)\s*$", p)
        if m2:
            item_name = (m2.group(1) or "").strip()
            if item_name:
                results.append(
                    {
                        "case_id": internal_case_id,
                        "free_item_name": item_name,
                        "quantity": None,
                        "unit": None,
                        "memo": memo,
                    }
                )

    return results


# -----------------------------
# API: CSV import
# -----------------------------
@app.post("/api/import-csv")
def import_csv():
    if "file" not in request.files:
        return jsonify({"ok": False, "error": "ファイルが選択されていません"}), 400

    f = request.files["file"]
    if not f.filename.lower().endswith(".csv"):
        return jsonify({"ok": False, "error": "CSVファイルを選択してください"}), 400

    try:
        raw = f.read()
        text = raw.decode("cp932")  # Shift_JIS系
        df = pd.read_csv(io.StringIO(text), dtype=str)

        df = normalize_headers(df)
        validate_headers(df)

        cases_df = build_surg_cases(df)

        conn = get_conn()
        cur = conn.cursor()

        imported_cases = 0
        imported_usage = 0

        try:
            cur.execute("BEGIN")

            # ext_case_id で UPSERT（内部case_idは維持）
            for _, row in cases_df.iterrows():
                ext_case_id = row["ext_case_id"]

                cur.execute("SELECT case_id FROM surg_cases WHERE ext_case_id = ?", (ext_case_id,))
                found = cur.fetchone()

                if found:
                    internal_case_id = int(found["case_id"])
                    cur.execute(
                        """
                        UPDATE surg_cases
                        SET patient_id = ?,
                            patient_name = ?,
                            surg_date = ?,
                            age = ?,
                            dept = ?,
                            surg_procedure = ?,
                            disease = ?
                        WHERE case_id = ?
                        """,
                        (
                            row["patient_id"],
                            row["patient_name"],
                            row["surg_date"],
                            row["age"],
                            row["dept"],
                            row["surg_procedure"],
                            row["disease"],
                            internal_case_id,
                        ),
                    )
                else:
                    cur.execute(
                        """
                        INSERT INTO surg_cases
                          (patient_id, patient_name, surg_date, age, dept, surg_procedure, disease, ext_case_id)
                        VALUES
                          (?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            row["patient_id"],
                            row["patient_name"],
                            row["surg_date"],
                            row["age"],
                            row["dept"],
                            row["surg_procedure"],
                            row["disease"],
                            ext_case_id,
                        ),
                    )
                    internal_case_id = int(cur.lastrowid)

                imported_cases += 1

                # remarks から usage 抽出 → 該当case_idを作り直す（重複防止）
                cur.execute("DELETE FROM case_usage WHERE case_id = ?", (internal_case_id,))
                usage_rows = parse_usage_from_remarks(internal_case_id, row.get("remarks", ""))

                for u in usage_rows:
                    cur.execute(
                        """
                        INSERT INTO case_usage (case_id, free_item_name, quantity, unit, memo)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (
                            u["case_id"],
                            u["free_item_name"],
                            str(u["quantity"]) if u["quantity"] is not None else None,
                            u["unit"],
                            u["memo"],
                        ),
                    )
                imported_usage += len(usage_rows)

            conn.commit()

        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

        return jsonify(
            {
                "ok": True,
                "message": "CSVインポート完了",
                "imported_cases": int(imported_cases),
                "imported_usage_rows": int(imported_usage),
            }
        )

    except UnicodeDecodeError:
        return jsonify({"ok": False, "error": "文字コードの読み取りに失敗しました（Shift_JIS / CP932想定）"}), 400
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        return jsonify({"ok": False, "error": f"インポート処理でエラー: {e}"}), 500


# -----------------------------
# API: list cases (for cases.html をDB化したい時に使う)
# -----------------------------
@app.get("/api/cases")
def api_cases():
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT
              case_id,
              ext_case_id,
              patient_id,
              patient_name,
              surg_date,
              age,
              dept,
              surg_procedure,
              disease
            FROM surg_cases
            ORDER BY surg_date DESC, patient_id ASC
            """
        )
        rows = [dict(r) for r in cur.fetchall()]
        return jsonify({"ok": True, "cases": rows})
    finally:
        conn.close()


@app.get("/api/cases/<int:case_id>/usage")
def api_case_usage(case_id: int):
    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT usage_id, case_id, free_item_name, quantity, unit, memo
            FROM case_usage
            WHERE case_id = ?
            ORDER BY usage_id ASC
            """,
            (case_id,),
        )
        rows = [dict(r) for r in cur.fetchall()]
        return jsonify({"ok": True, "usage": rows})
    finally:
        conn.close()


@app.put("/api/cases/<int:case_id>/usage")
def api_case_usage_replace(case_id: int):
    """
    body例:
      { "usage": [ { "free_item_name":"ガーゼ", "quantity":2, "unit":"枚", "memo":"..." }, ... ] }
    """
    data = request.get_json(silent=True) or {}
    usage = data.get("usage", [])
    if not isinstance(usage, list):
        return jsonify({"ok": False, "error": "usage は配列で送ってください"}), 400

    conn = get_conn()
    try:
        cur = conn.cursor()
        cur.execute("BEGIN")

        # 存在チェック（FKエラー回避）
        cur.execute("SELECT 1 FROM surg_cases WHERE case_id = ?", (case_id,))
        if cur.fetchone() is None:
            conn.rollback()
            return jsonify({"ok": False, "error": "症例が存在しません"}), 404

        cur.execute("DELETE FROM case_usage WHERE case_id = ?", (case_id,))

        inserted = 0
        for u in usage:
            name = str(u.get("free_item_name") or u.get("item_name") or "").strip()
            if not name:
                continue
            qty = u.get("quantity", None)
            unit = u.get("unit", None)
            memo = u.get("memo", None)

            cur.execute(
                """
                INSERT INTO case_usage (case_id, free_item_name, quantity, unit, memo)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    case_id,
                    name,
                    str(qty) if qty is not None else None,
                    unit,
                    memo,
                ),
            )
            inserted += 1

        conn.commit()
        return jsonify({"ok": True, "message": "保存しました", "inserted": inserted})
    except Exception as e:
        conn.rollback()
        return jsonify({"ok": False, "error": f"保存でエラー: {e}"}), 500
    finally:
        conn.close()


@app.get("/api/health")
def health():
    return jsonify({"ok": True, "db_path": str(DB_PATH)})


if __name__ == "__main__":
    app.run(debug=True)