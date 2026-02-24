from pathlib import Path
import io
import re
import sqlite3

import pandas as pd
from flask import Flask, send_from_directory, request, jsonify

# -----------------------------
# パス設定
# -----------------------------
BASE_DIR = Path(__file__).resolve().parent.parent   # surgery-cost/
DB_PATH = BASE_DIR / "surgDB.db"

app = Flask(__name__, static_folder=str(BASE_DIR), static_url_path="")


# -----------------------------
# 画面表示
# -----------------------------
@app.get("/")
def index():
    return send_from_directory(BASE_DIR, "index.html")


# -----------------------------
# DB接続
# -----------------------------
def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# -----------------------------
# ヘッダー定義（実CSVに合わせた）
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


def validate_headers(df: pd.DataFrame):
    missing = [c for c in REQUIRED_COLUMNS if c not in df.columns]
    if missing:
        raise ValueError("必須列が不足しています: " + ", ".join(missing))


def to_iso_date(val):
    if pd.isna(val):
        return None
    s = str(val).strip()
    if not s:
        return None
    dt = pd.to_datetime(s, errors="coerce")
    if pd.isna(dt):
        raise ValueError(f"手術実施日の形式が不正です: {s}")
    return dt.strftime("%Y-%m-%d")


def parse_int_safe(val):
    if pd.isna(val):
        return None
    s = str(val).strip()
    if s == "":
        return None
    m = re.search(r"\d+", s)
    return int(m.group()) if m else None


# -----------------------------
# surg_cases 用整形
# -----------------------------
def build_surg_cases(df: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame()
    out["case_id"] = df["症例ID"].astype(str).str.strip()
    out["patient_id"] = df["患者番号"].astype(str).str.strip()
    out["patient_name"] = df["患者氏名(漢字)"].astype(str).str.strip()
    out["surg_date"] = df["手術実施日"].apply(to_iso_date)
    out["age"] = df["年齢"].apply(parse_int_safe)
    out["dept"] = df["実施診療科"].astype(str).str.strip()
    out["surg_procedure"] = df["確定術式フリー検索"].astype(str).str.strip()
    out["disease"] = df["術後病名"].astype(str).str.strip()

    # 空のcase_id除外
    out = out[out["case_id"] != ""].copy()

    # 同一症例IDが複数あれば先頭を採用
    out = out.drop_duplicates(subset=["case_id"], keep="first")
    return out


# -----------------------------
# case_usage 用整形（Rロジック寄せ）
# -----------------------------
def parse_usage_from_remarks(case_id: str, remarks: str):
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

            # free_item_name は最初の [ の前を使う（Rコード寄せ）
            item_name = left.split("[")[0].strip()

            quantity = float(qty_str) if "." in qty_str else int(qty_str)

            if item_name:
                results.append({
                    "case_id": case_id,
                    "free_item_name": item_name,
                    "quantity": quantity,
                    "unit": unit,
                    "memo": memo,
                })
            continue

        # フォールバック（[]がない/崩れている）
        m2 = re.match(r"^★\s*(.*?)\s*$", p)
        if m2:
            item_name = (m2.group(1) or "").strip()
            if item_name:
                results.append({
                    "case_id": case_id,
                    "free_item_name": item_name,
                    "quantity": None,
                    "unit": None,
                    "memo": memo,
                })

    return results


def build_case_usage(df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for _, r in df.iterrows():
        case_id = str(r["症例ID"]).strip()
        if not case_id:
            continue
        rows.extend(parse_usage_from_remarks(case_id, r.get("リマークス（看護）", "")))

    out = pd.DataFrame(rows, columns=["case_id", "free_item_name", "quantity", "unit", "memo"])
    if len(out) == 0:
        return out

    # 完全重複を除外
    out = out.drop_duplicates(subset=["case_id", "free_item_name", "memo"], keep="first")
    return out


# -----------------------------
# CSVインポートAPI
# -----------------------------
@app.post("/api/import-csv")
def import_csv():
    if "file" not in request.files:
        return jsonify({"ok": False, "error": "ファイルが選択されていません"}), 400

    f = request.files["file"]
    if not f.filename.lower().endswith(".csv"):
        return jsonify({"ok": False, "error": "CSVファイルを選択してください"}), 400

    try:
        # Shift_JIS（Windows系CSVは cp932）
        raw = f.read()
        text = raw.decode("cp932")
        df = pd.read_csv(io.StringIO(text), dtype=str)

        df = normalize_headers(df)
        validate_headers(df)

        surg_cases_df = build_surg_cases(df)
        case_usage_df = build_case_usage(df)

        conn = get_conn()
        cur = conn.cursor()

        try:
            cur.execute("BEGIN")

            # surg_cases: case_id で UPSERT
            for _, row in surg_cases_df.iterrows():
                cur.execute("""
                    INSERT INTO surg_cases
                    (case_id, patient_id, patient_name, surg_date, age, dept, surg_procedure, disease)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(case_id) DO UPDATE SET
                        patient_id=excluded.patient_id,
                        patient_name=excluded.patient_name,
                        surg_date=excluded.surg_date,
                        age=excluded.age,
                        dept=excluded.dept,
                        surg_procedure=excluded.surg_procedure,
                        disease=excluded.disease
                """, (
                    row["case_id"],
                    row["patient_id"],
                    row["patient_name"],
                    row["surg_date"],
                    row["age"],
                    row["dept"],
                    row["surg_procedure"],
                    row["disease"],
                ))

            # case_usage: 対象case_idを一旦削除して再登録（重複防止）
            target_case_ids = surg_cases_df["case_id"].astype(str).tolist()
            if target_case_ids:
                placeholders = ",".join(["?"] * len(target_case_ids))
                cur.execute(f"DELETE FROM case_usage WHERE case_id IN ({placeholders})", target_case_ids)

            for _, row in case_usage_df.iterrows():
                cur.execute("""
                    INSERT INTO case_usage (case_id, free_item_name, quantity, unit, memo)
                    VALUES (?, ?, ?, ?, ?)
                """, (
                    row["case_id"],
                    row["free_item_name"],
                    str(row["quantity"]) if pd.notna(row["quantity"]) else None,
                    row["unit"],
                    row["memo"],
                ))

            conn.commit()

        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

        return jsonify({
            "ok": True,
            "message": "CSVインポート完了",
            "imported_cases": int(len(surg_cases_df)),
            "imported_usage_rows": int(len(case_usage_df)),
        })

    except UnicodeDecodeError:
        return jsonify({"ok": False, "error": "文字コードの読み取りに失敗しました（Shift_JIS / CP932想定）"}), 400
    except ValueError as e:
        return jsonify({"ok": False, "error": str(e)}), 400
    except Exception as e:
        return jsonify({"ok": False, "error": f"インポート処理でエラー: {e}"}), 500


# -----------------------------
# 動作確認用（任意）
# -----------------------------
@app.get("/api/health")
def health():
    return jsonify({"ok": True, "db_path": str(DB_PATH)})


if __name__ == "__main__":
    app.run(debug=True)