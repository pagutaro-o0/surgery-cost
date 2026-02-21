document.addEventListener("DOMContentLoaded", () => {
  const fileInput =
    document.querySelector('input[type="file"]') ||
    document.getElementById("csvFile");

  const importBtn =
    document.getElementById("importBtn") ||
    [...document.querySelectorAll("button")].find((b) =>
      (b.textContent || "").includes("インポート")
    );

  const patientListBtn =
    document.getElementById("goPatientsBtn") ||
    [...document.querySelectorAll("button")].find((b) =>
      (b.textContent || "").includes("患者一覧")
    );

  // 結果表示エリアを自動で作る（なければ）
  let resultBox = document.getElementById("importResult");
  if (!resultBox) {
    resultBox = document.createElement("div");
    resultBox.id = "importResult";
    resultBox.style.marginTop = "12px";
    resultBox.style.padding = "10px 12px";
    resultBox.style.border = "1px solid #2c3e66";
    resultBox.style.background = "#fff";
    resultBox.style.whiteSpace = "pre-wrap";
    resultBox.style.fontSize = "14px";

    // ファイル選択の近くに差し込み（できるだけ自然な場所）
    const target = (importBtn && importBtn.parentElement) || document.body;
    target.appendChild(resultBox);
  }

  function setResult(msg, isError = false) {
    resultBox.textContent = msg;
    resultBox.style.color = isError ? "#b00020" : "#111";
  }

  if (!fileInput || !importBtn) {
    console.warn("CSVインポート用の input/button が見つかりません");
    return;
  }

  importBtn.addEventListener("click", async () => {
    if (!fileInput.files || fileInput.files.length === 0) {
      setResult("CSVファイルを選択してください。", true);
      return;
    }

    const file = fileInput.files[0];
    const formData = new FormData();
    formData.append("file", file);

    const originalText = importBtn.textContent;
    importBtn.disabled = true;
    importBtn.textContent = "インポート中...";

    try {
      const res = await fetch("/api/import-csv", {
        method: "POST",
        body: formData,
      });

      const data = await res.json();

      if (!res.ok || !data.ok) {
        throw new Error(data.error || "インポートに失敗しました");
      }

      setResult(
        `✅ ${data.message}\n症例: ${data.imported_cases}件\n物品: ${data.imported_usage_rows}件`
      );
    } catch (err) {
      console.error(err);
      setResult(`❌ ${err.message}`, true);
    } finally {
      importBtn.disabled = false;
      importBtn.textContent = originalText;
    }
  });

  // 既存UIの「患者一覧へ」ボタンがあれば遷移（任意）
  if (patientListBtn) {
    patientListBtn.addEventListener("click", () => {
      window.location.href = "cases.html";
    });
  }
});