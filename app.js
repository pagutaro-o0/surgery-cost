// app.js
const LS_KEYS = {
  masterItems: "sc_master_items",
  cases: "sc_cases",
  seq: "sc_seq"
};

function todayISO(){
  const d = new Date();
  const yyyy = d.getFullYear();
  const mm = String(d.getMonth()+1).padStart(2,"0");
  const dd = String(d.getDate()).padStart(2,"0");
  return `${yyyy}-${mm}-${dd}`;
}
function toJPDate(iso){
  if(!iso) return "";
  const [y,m,d] = iso.split("-");
  return `${y}/${Number(m)}/${Number(d)}`;
}

function load(key, fallback){
  try{
    const s = localStorage.getItem(key);
    if(!s) return fallback;
    return JSON.parse(s);
  }catch{
    return fallback;
  }
}
function save(key, val){
  localStorage.setItem(key, JSON.stringify(val));
}

function nextId(prefix){
  const seq = load(LS_KEYS.seq, { caseNo: 1000 });
  seq.caseNo += 1;
  save(LS_KEYS.seq, seq);
  return `${prefix}${seq.caseNo}`;
}

function seedIfEmpty(){
  const masters = load(LS_KEYS.masterItems, null);
  if(!masters){
    save(LS_KEYS.masterItems, [
      { id:"m1", name:"皮膚切開数", unit:"皮切", order:1, start:"2026-02-01", end:"2099-12-31", memo:"" },
      { id:"m2", name:"ナビゲーション", unit:"回", order:2, start:"2026-02-01", end:"2099-12-31", memo:"" }
    ]);
  }
  const cases = load(LS_KEYS.cases, null);
  if(!cases){
    save(LS_KEYS.cases, [
      {
        id: "c1001",
        caseDate: "2026-02-12",
        patientId: "P001",
        staffId: "S001",
        dept: "整形外科",
        caseNo: "A-0001",
        deleted: true,
        masterLines: [
          { masterId:"m1", name:"皮膚切開数", qty:1, unit:"皮切", memo:"" },
          { masterId:"m2", name:"ナビゲーション", qty:1, unit:"回", memo:"" }
        ],
        freeLines: []
      },
      {
        id: "c1002",
        caseDate: "2026-02-13",
        patientId: "P002",
        staffId: "S002",
        dept: "整形外科",
        caseNo: "A-0002",
        deleted: false,
        masterLines: [
          { masterId:"m1", name:"皮膚切開数", qty:1, unit:"皮切", memo:"" }
        ],
        freeLines: []
      }
    ]);
  }
}

function getMasters(){
  const items = load(LS_KEYS.masterItems, []);
  // orderでソート
  return [...items].sort((a,b)=> (a.order??999) - (b.order??999));
}
function setMasters(items){ save(LS_KEYS.masterItems, items); }

function getCases(){ return load(LS_KEYS.cases, []); }
function setCases(items){ save(LS_KEYS.cases, items); }

function qs(sel){ return document.querySelector(sel); }
function qsa(sel){ return Array.from(document.querySelectorAll(sel)); }

function getUrlParam(name){
  const u = new URL(location.href);
  return u.searchParams.get(name);
}