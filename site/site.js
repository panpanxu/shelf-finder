// 静态站的浏览器端逻辑：搜索、照片页高亮命中、豆瓣链接按设备换地址。
// 匹配规则和服务端一致：去空白、转小写、繁转简；关键字正好是标签名时按标签命中；
// 否则在书脊文字里找允许少量编辑错误的子串（每五个字允许一处）。
(function () {
  const DATA = window.SHELF_DATA;
  const page = document.body.dataset.page;
  const root = document.body.dataset.root;
  const params = new URLSearchParams(location.search);
  const q = (params.get("q") || "").trim();
  const mobile = /Mobi|Android|iPhone|iPad/i.test(navigator.userAgent);

  function normalize(text) {
    return [...text.replace(/\s+/g, "").toLowerCase()].map((c) => DATA.t2s[c] || c).join("");
  }

  // needle 在 hay 任意位置作为子串出现所需的最少编辑次数（增、删、改）
  function minErrors(needle, hay) {
    const n = needle.length;
    let prev = Array.from({ length: n + 1 }, (_, i) => i);
    let best = n;
    for (let j = 1; j <= hay.length; j++) {
      const cur = [0];
      for (let i = 1; i <= n; i++) {
        const same = needle[i - 1] === hay[j - 1] ? 0 : 1;
        cur[i] = Math.min(prev[i] + 1, cur[i - 1] + 1, prev[i - 1] + same);
      }
      best = Math.min(best, cur[n]);
      prev = cur;
    }
    return best;
  }

  function search(keyword) {
    const needle = normalize(keyword);
    if (!needle) return [];
    const allowed = Math.floor(needle.length / 5);
    const tag = DATA.vocabulary.find((t) => normalize(t) === needle);
    const hits = [];
    for (const spine of DATA.spines) {
      if (tag && spine.tags.includes(tag)) {
        hits.push({ spine, errors: 0 });
        continue;
      }
      const errors = minErrors(needle, spine.key);
      if (errors <= allowed) hits.push({ spine, errors });
    }
    hits.sort((a, b) => a.errors - b.errors || a.spine.photo - b.spine.photo || a.spine.order - b.spine.order);
    return hits;
  }

  function doubanUrl(query) {
    const enc = encodeURIComponent(query);
    return mobile
      ? `https://m.douban.com/search/?query=${enc}&type=book`
      : `https://search.douban.com/book/subject_search?search_text=${enc}&cat=1001`;
  }

  function el(tag, attrs, children) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) node.setAttribute(k, v);
    for (const child of children || []) node.append(child);
    return node;
  }

  function tagLinks(tags) {
    return tags.map((t) => el("a", { class: "tag", href: `${root}search.html?q=${encodeURIComponent(t)}` }, [t]));
  }

  // 搜索框回填关键字
  const input = document.querySelector('input[name="q"]');
  if (input && q) input.value = q;

  if (page === "search") {
    const results = document.getElementById("results");
    const hits = search(q);
    document.title = `${q} · 找书`;
    if (!hits.length) {
      results.append(el("p", { class: "muted" }, [`没有找到包含「${q}」的书。`]));
    }
    const groups = new Map();
    for (const hit of hits) {
      if (!groups.has(hit.spine.photo)) groups.set(hit.spine.photo, []);
      groups.get(hit.spine.photo).push(hit);
    }
    const photoName = Object.fromEntries(DATA.photos.map((p) => [p.id, p.name]));
    for (const [photoId, group] of groups) {
      const href = `photos/${photoId}.html?q=${encodeURIComponent(q)}`;
      results.append(el("h2", {}, [el("a", { href }, [photoName[photoId]])]));
      const list = el("ul", { class: "plain card" });
      for (const { spine } of group) {
        list.append(el("li", {}, [
          el("a", { href: `${href}#spine-${spine.id}` }, [spine.text]),
          ...tagLinks(spine.tags),
          el("a", { class: "douban", href: doubanUrl(spine.dq), target: "_blank", rel: "noopener", title: "在豆瓣搜索这本书" }, ["豆瓣"]),
        ]));
      }
      results.append(list);
    }
  }

  if (page === "photo") {
    const photoId = Number(document.getElementById("layout").dataset.photo);
    if (q) {
      const back = document.getElementById("nav-back");
      back.href = `${root}search.html?q=${encodeURIComponent(q)}`;
      back.textContent = `← 搜索结果「${q}」`;
      const hitIds = new Set(search(q).filter((h) => h.spine.photo === photoId).map((h) => h.spine.id));
      const note = document.getElementById("hit-note");
      note.hidden = false;
      note.textContent = hitIds.size ? `「${q}」在这张照片上命中 ${hitIds.size} 本书。` : `这张照片上没有「${q}」。`;
      if (hitIds.size) document.getElementById("viewer").classList.add("has-hits");
      for (const id of hitIds) {
        document.querySelectorAll(`[data-id="${id}"]`).forEach((node) => node.classList.add("hit"));
      }
    }
    if (mobile) {
      document.querySelectorAll("a.douban[data-query]").forEach((a) => { a.href = doubanUrl(a.dataset.query); });
    }
  }
})();
