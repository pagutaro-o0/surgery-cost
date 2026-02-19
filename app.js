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
  cases: "surg_cases",
  usage: "case_usage"
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

function seedIfEmpty(){
  // CSVインポート前提：ここでは何もしない
}

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

/* ========= CSV parser ========= */
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

function normalizeHeader(h){ return (h||"").trim(); }

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

/* 期待する見出し:
   症例ID, 患者番号, 患者氏名（漢字）, 手術実施日, 年齢,
   実施診療科, 確定術式フリー検索, 術後病名, リマークス（看護）
*/
function importCasesFromCSV(csvText){
  const rows = parseCSV(csvText);
  if(rows.length < 2) throw new Error("CSVにデータがありません");

  const header = rows[0].map(normalizeHeader);
  const idx = (name) => header.indexOf(name);

  const required = [
    "症例ID","患者番号","患者氏名（漢字）","手術実施日","年齢",
    "実施診療科","確定術式フリー検索","術後病名","リマークス（看護）"
  ];
  for(const r of required){
    if(idx(r) === -1) throw new Error(`CSV見出しが見つかりません: ${r}`);
  }

  const imported = [];
  for(let i=1;i<rows.length;i++){
    const r = rows[i];
    if(r.length === 1 && !r[0]) continue;

    const case_id = String(r[idx("症例ID")]||"").trim();
    if(!case_id) continue;

    imported.push({
      case_id,
      patient_id: String(r[idx("患者番号")]||"").trim(),
      patient_name: String(r[idx("患者氏名（漢字）")]||"").trim(),
      surg_date: toISODateFromMaybeJP(String(r[idx("手術実施日")]||"")),
      age: Number(String(r[idx("年齢")]||"").trim()) || null,
      dept: String(r[idx("実施診療科")]||"").trim(),
      surg_procedure: String(r[idx("確定術式フリー検索")]||"").trim(),
      disease: String(r[idx("術後病名")]||"").trim(),
      remarks: String(r[idx("リマークス（看護）")]||"").trim(),
      deleted: false
    });
  }

  const current = getCases();
  const map = new Map(current.map(c => [c.case_id, c]));
  imported.forEach(c => map.set(c.case_id, c));
  const merged = Array.from(map.values());

  merged.sort((a,b)=>{
    const ad = a.surg_date || "";
    const bd = b.surg_date || "";
    if(ad !== bd) return bd.localeCompare(ad);
    return String(a.patient_id||"").localeCompare(String(b.patient_id||""));
  });

  setCases(merged);
  return { imported: imported.length, total: merged.length };
}

/* ========= Usage ========= */
function getUsageByCaseId(caseId){
  const all = getUsages();
  return all.filter(u => u.case_id === caseId);
}
function setUsageForCaseId(caseId, lines){
  const all = getUsages().filter(u => u.case_id !== caseId);
  const normalized = lines.map(l => ({
    case_id: caseId,
    item_name: String(l.item_name||"").trim(),
    quantity: Number(l.quantity)||0,
    unit: String(l.unit||"").trim(),
    memo: String(l.memo||"").trim()
  })).filter(l => l.item_name !== "");
  setUsages(all.concat(normalized));
}