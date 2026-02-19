/* ========= Helpers ========= */
const qs  = (s, el=document) => el.querySelector(s);
const qsa = (s, el=document) => Array.from(el.querySelectorAll(s));

function todayISO(){
  const d = new Date();
  const y = d.getFullYear();
  const m = String(d.getMonth()+1).padStart(2,"0");
  const dd = String(d.getDate()).padStart(2,"0");
  return `${y}-${m}-${dd}`;
}
function toJPDate(iso){
  if(!iso) return "";
  const [y,m,d] = iso.split("-");
  return `${Number(y)}/${Number(m)}/${Number(d)}`;
}
function getUrlParam(key){
  const u = new URL(location.href);
  return u.searchParams.get(key);
}

/* ========= Storage ========= */
const LS_KEYS = {
  cases: "sc_cases",        // 患者一覧（症例） ←ここに統一
  usage: "case_usage"       // 消耗品
};

function load(key, fallback){
  try{
    const raw = localStorage.getItem(key);
    if(!raw) return fallback;
    return JSON.parse(raw);
  }catch{
    return fallback;
  }
}
function save(key, value){
  localStorage.setItem(key, JSON.stringify(value));
}

function getCases(){ return load(LS_KEYS.cases, []); }
function setCases(v){ save(LS_KEYS.cases, v); }

function getUsages(){ return load(LS_KEYS.usage, []); }
function setUsages(v){ save(LS_KEYS.usage, v); }

function seedIfEmpty(){}

/* ========= Header ========= */
function renderAppHeader({ active="cases" } = {}){
  const el = qs("#appHeader");
  if(!el) return;

  const a = (key) => key === active ? "active" : "";
  el.innerHTML = `
    <div class="topbar">
      <div class="logo">✂️</div>
      <div class="app-title">手術コスト算定管理</div>
      <div class="nav">
        <a class="${a("import")}" href="./index.html">データインポート</a>
        <a class="${a("cases")}" href="./cases.html">患者一覧</a>
      </div>
    </div>
  `;
}

/* ========= CSV parsing ========= */
function parseCSV(text){
  const rows = [];
  let cur = "", inQ = false;
  const line = [];

  for(let i=0;i<text.length;i++){
    const ch = text[i];
    const next = text[i+1];

    if(ch === '"'){
      if(inQ && next === '"'){ cur += '"'; i++; }
      else inQ = !inQ;
      continue;
    }
    if(!inQ && ch === ","){
      line.push(cur); cur = "";
      continue;
    }
    if(!inQ && ch === "\n"){
      line.push(cur); cur = "";
      rows.push(line.splice(0));
      continue;
    }
    if(ch === "\r") continue;
    cur += ch;
  }
  if(cur.length || line.length){
    line.push(cur);
    rows.push(line.splice(0));
  }
  return rows;
}

function normHeader(s){
  return (s||"")
    .replace(/\uFEFF/g,"")       // BOM除去
    .replace(/[ 　]+/g,"")       // 半角/全角スペース除去
    .trim();
}

function toISODateFromMaybeJP(s){
  const t = (s||"").trim();
  if(!t) return "";
  if(t.includes("-")){
    const [y,m,d] = t.split("-");
    return `${y.padStart(4,"0")}-${m.padStart(2,"0")}-${d.padStart(2,"0")}`;
  }
  if(t.includes("/")){
    const [y,m,d] = t.split("/");
    return `${y.padStart(4,"0")}-${String(m).padStart(2,"0")}-${String(d).padStart(2,"0")}`;
  }
  return t;
}

function buildHeaderIndex(headerRow){
  const header = headerRow.map(normHeader);
  const map = new Map();
  header.forEach((h,i)=> map.set(h,i));

  // 同義語で探す（1つでも当たればOK）
  const find = (...cands)=>{
    for(const c of cands){
      const k = normHeader(c);
      if(map.has(k)) return map.get(k);
    }
    return -1;
  };

  const COL = {
    case_id:        find("症例ID","症例ＩＤ","case_id","caseId"),
    patient_id:     find("患者番号","患者ID","patient_id","patientId"),
    patient_name:   find("患者氏名（漢字）","患者氏名(漢字)","患者氏名","patient_name","patientName"),
    surg_date:      find("手術実施日","手術日","実施日","surg_date","surgDate"),
    age:            find("年齢","age"),
    dept:           find("実施診療科","診療科","dept"),
    surg_procedure: find("確定術式フリー検索","確定術式","術式","surg_procedure","procedure"),
    disease:        find("術後病名","病名","disease"),
    remarks:        find("リマークス（看護）","リマークス(看護)","リマークス","remarks")
  };

  const required = ["case_id","patient_id","patient_name","surg_date","age","dept","surg_procedure","disease"];
  for(const k of required){
    if(COL[k] === -1) throw new Error(`CSV見出しが見つかりません: ${k}`);
  }
  return COL;
}

// UUIDでも数字でも一意にする（stringで保持）
function normalizeCaseId(v){
  return String(v ?? "").trim();
}

function importCasesFromCSV(csvText){
  const rows = parseCSV(csvText);
  if(rows.length < 2) throw new Error("CSVにデータがありません");

  const COL = buildHeaderIndex(rows[0]);

  const imported = [];
  for(let i=1;i<rows.length;i++){
    const r = rows[i];
    if(r.length === 1 && !r[0]) continue;

    const caseIdRaw = normalizeCaseId(r[COL.case_id]);
    if(!caseIdRaw) continue;

    imported.push({
      case_id: caseIdRaw, // 文字列で統一（UUIDでもOK）
      patient_id: Number(String(r[COL.patient_id]||"").trim()) || String(r[COL.patient_id]||"").trim(),
      patient_name: String(r[COL.patient_name]||"").trim(),
      surg_date: toISODateFromMaybeJP(String(r[COL.surg_date]||"")),
      age: Number(String(r[COL.age]||"").trim()) || null,
      dept: String(r[COL.dept]||"").trim(),
      surg_procedure: String(r[COL.surg_procedure]||"").trim(),
      disease: String(r[COL.disease]||"").trim(),
      remarks: (COL.remarks !== -1) ? String(r[COL.remarks]||"").trim() : "",
      deleted: false
    });
  }

  // case_id uniqueでupsert
  const current = getCases();
  const map = new Map(current.map(c => [String(c.case_id), c]));
  imported.forEach(c => map.set(String(c.case_id), c));
  const merged = Array.from(map.values());

  // 並び：日付 desc → 患者番号 asc
  merged.sort((a,b)=>{
    const ad = a.surg_date || "";
    const bd = b.surg_date || "";
    if(ad !== bd) return bd.localeCompare(ad);
    return String(a.patient_id||"").localeCompare(String(b.patient_id||""));
  });

  setCases(merged);
  return { imported: imported.length, total: merged.length };
}

/* ========= Usage =========
   case_usage: { case_id, free_item_name, quantity, memo }
*/
function getUsageByCaseId(caseId){
  const key = String(caseId);
  return getUsages().filter(u => String(u.case_id) === key);
}

function setUsageForCaseId(caseId, lines){
  const key = String(caseId);
  const rest = getUsages().filter(u => String(u.case_id) !== key);

  const normalized = (lines || []).map(l => ({
    case_id: key,
    item_name: String(l.item_name || l.free_item_name || "").trim(),
    quantity: Number(l.quantity) || 0,
    unit: String(l.unit || "").trim(),
    memo: String(l.memo || "").trim()
  })).filter(x => x.item_name !== "");

  setUsages(rest.concat(normalized));
}
// ★付きだけ抽出して [{name, qty}] を返す
function extractStarNameQty(text){
  const t = String(text || "");
  const re = /★\s*([^\n★]+)/g;     // ★から次の★/改行まで
  const out = [];
  let m;

  while((m = re.exec(t)) !== null){
    const block = m[1].trim();     // 例: 生理食塩水250ml[[1]本,標本摘出...]
    
    // 品目名：[[...]] の手前、カンマより前
    const name = block
      .split("[[")[0]
      .split(",")[0]
      .trim();

    // 数量：[[1]本] / [[1]本, ...] から 1 を拾う（無ければ1）
    let qty = 1;
    const q = block.match(/\[\[\s*\[?\s*(\d+)\s*\]?\s*[^\]]*\]\]/);
    if(q) qty = Number(q[1]);

    if(name) out.push({ name, qty });
  }

  // 同名は合算
  const map = new Map();
  for(const x of out){
    map.set(x.name, (map.get(x.name) || 0) + x.qty);
  }
  return Array.from(map.entries()).map(([name, qty]) => ({ name, qty }));
}
const remarks = "★生理食塩水250ml[[1]本]\n★生理食塩水250ml[[1]本]";
console.log(extractStarNameQty(remarks));
// => [{ name: "生理食塩水250ml", qty: 2 }]