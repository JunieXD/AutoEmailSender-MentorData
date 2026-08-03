const state = {
  catalog: null,
};

const nodes = {
  version: document.querySelector("#dataset-version"),
  recordCount: document.querySelector("#record-count"),
  generatedAt: document.querySelector("#generated-at"),
  catalog: document.querySelector("#catalog"),
  search: document.querySelector("#catalog-search"),
  error: document.querySelector("#load-error"),
};

function formatTime(value) {
  const instant = new Date(value);
  return Number.isNaN(instant.valueOf()) ? value : instant.toLocaleString("zh-CN");
}

function unitMatches(unit, keyword) {
  return unit.name.toLocaleLowerCase().includes(keyword);
}

function renderCatalog() {
  nodes.catalog.replaceChildren();
  if (!state.catalog) {
    return;
  }
  const keyword = nodes.search.value.trim().toLocaleLowerCase();
  let visibleCount = 0;
  for (const university of state.catalog.universities) {
    const universityMatches = university.name.toLocaleLowerCase().includes(keyword);
    const units = keyword
      ? university.units.filter((unit) => universityMatches || unitMatches(unit, keyword))
      : university.units;
    if (keyword && !universityMatches && units.length === 0) {
      continue;
    }

    const article = document.createElement("article");
    article.className = "university";
    const header = document.createElement("div");
    header.className = "university-header";
    const title = document.createElement("h3");
    title.textContent = university.name;
    const count = document.createElement("span");
    count.className = "badge";
    count.textContent = `${university.record_count} 位`;
    header.append(title, count);
    article.append(header);

    const unitList = document.createElement("div");
    unitList.className = "units";
    for (const unit of units) {
      const row = document.createElement("div");
      row.className = "unit";
      const link = document.createElement("a");
      link.href = `datasets/${encodeURIComponent(state.catalog.dataset_version)}/${unit.path}`;
      link.textContent = unit.name;
      const unitCount = document.createElement("span");
      unitCount.className = "badge";
      unitCount.textContent = `${unit.record_count}`;
      row.append(link, unitCount);
      unitList.append(row);
    }
    article.append(unitList);
    nodes.catalog.append(article);
    visibleCount += 1;
  }

  if (visibleCount === 0) {
    const empty = document.createElement("p");
    empty.className = "empty";
    empty.textContent = state.catalog.universities.length === 0 ? "当前尚未发布导师数据。" : "没有匹配的学校或学院。";
    nodes.catalog.append(empty);
  }
}

async function loadCatalog() {
  try {
    const latestResponse = await fetch("latest.json", { cache: "no-cache" });
    if (!latestResponse.ok) {
      throw new Error(`latest.json 返回 ${latestResponse.status}`);
    }
    const latest = await latestResponse.json();
    const catalogResponse = await fetch(latest.catalog_path, { cache: "no-cache" });
    if (!catalogResponse.ok) {
      throw new Error(`catalog.json 返回 ${catalogResponse.status}`);
    }
    state.catalog = await catalogResponse.json();
    nodes.version.textContent = state.catalog.dataset_version;
    nodes.recordCount.textContent = String(state.catalog.record_count);
    nodes.generatedAt.textContent = formatTime(state.catalog.generated_at);
    renderCatalog();
  } catch (error) {
    nodes.error.hidden = false;
    nodes.error.textContent = `社区目录暂时无法读取：${error instanceof Error ? error.message : "未知错误"}`;
    nodes.version.textContent = "读取失败";
  }
}

nodes.search.addEventListener("input", renderCatalog);
void loadCatalog();

