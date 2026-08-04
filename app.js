const state = {
  catalog: null,
  searchFrame: null,
};

const nodes = {
  version: document.querySelector("#dataset-version"),
  recordCount: document.querySelector("#record-count"),
  universityCount: document.querySelector("#university-count"),
  unitCount: document.querySelector("#unit-count"),
  generatedAt: document.querySelector("#generated-at"),
  previewUniversity: document.querySelector("#preview-university"),
  previewRecords: document.querySelector("#preview-records"),
  previewUnit: document.querySelector("#preview-unit"),
  catalog: document.querySelector("#catalog"),
  catalogStatus: document.querySelector("#catalog-status"),
  search: document.querySelector("#catalog-search"),
  error: document.querySelector("#load-error"),
};

function formatDate(value) {
  const instant = new Date(value);
  return Number.isNaN(instant.valueOf())
    ? value
    : new Intl.DateTimeFormat("zh-CN", { dateStyle: "long" }).format(instant);
}

function unitMatches(unit, keyword) {
  return unit.name.toLocaleLowerCase().includes(keyword);
}

function renderCatalog() {
  if (!state.catalog) {
    return;
  }
  const fragment = document.createDocumentFragment();
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
    article.className = "home-university";
    const header = document.createElement("div");
    header.className = "home-university-header";
    const title = document.createElement("h3");
    title.textContent = university.name;
    const count = document.createElement("span");
    count.className = "home-count";
    count.textContent = `${university.record_count} 位导师`;
    header.append(title, count);
    article.append(header);

    const unitList = document.createElement("ul");
    unitList.className = "home-units";
    for (const unit of units) {
      const row = document.createElement("li");
      const name = document.createElement("span");
      name.textContent = unit.name;
      const unitCount = document.createElement("span");
      unitCount.className = "home-unit-count";
      unitCount.textContent = `${unit.record_count} 位`;
      row.append(name, unitCount);
      unitList.append(row);
    }
    article.append(unitList);
    fragment.append(article);
    visibleCount += 1;
  }

  if (visibleCount === 0) {
    const empty = document.createElement("p");
    empty.className = "empty home-empty";
    empty.textContent = state.catalog.universities.length
      ? "没有匹配的学校或学院。"
      : "当前尚未发布导师数据。";
    fragment.append(empty);
  }
  nodes.catalog.replaceChildren(fragment);
}

function safeCatalogUrl(path) {
  if (typeof path !== "string") {
    throw new Error("目录地址无效");
  }
  const url = new URL(path, window.location.href);
  const base = new URL("./releases/", window.location.href);
  if (url.origin !== base.origin || !url.pathname.startsWith(base.pathname)) {
    throw new Error("目录地址不属于本站数据集");
  }
  return url;
}

function validateCatalog(catalog) {
  if (
    !catalog ||
    typeof catalog.dataset_version !== "string" ||
    !Number.isSafeInteger(catalog.record_count) ||
    !Array.isArray(catalog.universities)
  ) {
    throw new Error("社区目录格式无效");
  }
  for (const university of catalog.universities) {
    if (
      !university ||
      typeof university.name !== "string" ||
      !Number.isSafeInteger(university.record_count) ||
      !Array.isArray(university.units)
    ) {
      throw new Error("高校目录格式无效");
    }
    for (const unit of university.units) {
      if (
        !unit ||
        typeof unit.name !== "string" ||
        !Number.isSafeInteger(unit.record_count)
      ) {
        throw new Error("学院目录格式无效");
      }
    }
  }
}

async function loadCatalog() {
  try {
    const latestResponse = await fetch("latest.json", { cache: "no-cache" });
    if (!latestResponse.ok) {
      throw new Error(`目录索引返回 ${latestResponse.status}`);
    }
    const latest = await latestResponse.json();
    const catalogResponse = await fetch(safeCatalogUrl(latest.catalog_path), { cache: "force-cache" });
    if (!catalogResponse.ok) {
      throw new Error(`院校目录返回 ${catalogResponse.status}`);
    }
    const catalog = await catalogResponse.json();
    validateCatalog(catalog);
    state.catalog = catalog;
    const unitCount = catalog.universities.reduce(
      (total, university) => total + (Array.isArray(university.units) ? university.units.length : 0),
      0,
    );
    nodes.version.textContent = catalog.dataset_version;
    nodes.recordCount.textContent = String(catalog.record_count);
    nodes.universityCount.textContent = String(catalog.universities.length);
    nodes.unitCount.textContent = String(unitCount);
    nodes.generatedAt.textContent = formatDate(catalog.generated_at);
    const firstUniversity = catalog.universities[0];
    if (firstUniversity) {
      nodes.previewUniversity.textContent = firstUniversity.name;
      nodes.previewRecords.textContent = String(firstUniversity.record_count);
      nodes.previewUnit.textContent = firstUniversity.units?.[0]?.name || "选择学院或单位";
    }
    nodes.catalogStatus.hidden = true;
    renderCatalog();
  } catch (error) {
    nodes.catalogStatus.hidden = true;
    nodes.error.hidden = false;
    nodes.error.textContent = `社区目录暂时无法读取：${error instanceof Error ? error.message : "未知错误"}`;
    nodes.version.textContent = "读取失败";
  }
}

nodes.search.addEventListener("input", () => {
  if (state.searchFrame !== null) {
    cancelAnimationFrame(state.searchFrame);
  }
  state.searchFrame = requestAnimationFrame(() => {
    state.searchFrame = null;
    renderCatalog();
  });
});
void loadCatalog();
