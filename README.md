# surgery-cost

手術コスト算定管理（UIプロトタイプ / HTML・CSS・JavaScript）

## 構成
- `cases.html`：手術一覧（検索・参照/修正）
- `case-entry.html`：手術入力（マスタ項目/自由記載、+/- 操作）
- `master-item.html`：マスタ編集（表示日でフィルタ、表示順登録）
- `styles.css`：共通スタイル
- `app.js`：共通ロジック（localStorage 疑似DB）

## 起動方法（ローカル）
### PythonでHTTPサーバを起動
```bash
python3 -m http.server 8000
