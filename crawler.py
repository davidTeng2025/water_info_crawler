# -*- coding: utf-8 -*-
"""
通过模拟点击抓取国家水质自动综合监管平台所有页面数据。
使用 Playwright 打开页面，遍历可点击入口，提取表格与列表，供 export_excel 写入 Excel。
"""
import asyncio
import random
import re
from pathlib import Path
from urllib.parse import urljoin, urlparse

try:
    from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeout
except ImportError:
    raise ImportError("请先安装: pip install playwright && playwright install chromium")

import config


def _normalize_href(base_url, href):
    if not href or href.startswith("javascript:") or href == "#":
        return None
    return urljoin(base_url, href)


def _same_origin(base_url, url):
    if not url:
        return False
    p1, p2 = urlparse(base_url), urlparse(url)
    return (p1.scheme, p1.netloc) == (p2.scheme, p2.netloc)


async def _random_wait(max_ms=5000):
    """相邻操作间随机等待，防反爬。"""
    ms = getattr(config, "RANDOM_WAIT_MAX_MS", 5000)
    if max_ms is not None:
        ms = min(ms, max_ms)
    sec = random.uniform(0, ms / 1000.0)
    await asyncio.sleep(sec)


async def _wait_for_table_loaded(frame, min_rows=5, timeout_ms=12000):
    """等待 frame 内表格加载出至少 min_rows 行，或超时。"""
    wait_ms = getattr(config, "TABLE_LOAD_WAIT_MS", 12000)
    if timeout_ms is not None:
        wait_ms = min(wait_ms, timeout_ms)
    try:
        await frame.wait_for_selector(
            f"table tr:nth-child({min_rows})",
            timeout=wait_ms,
        )
    except Exception:
        await asyncio.sleep(wait_ms / 1000.0)
    await _random_wait(getattr(config, "RANDOM_WAIT_MAX_MS", 5000))


async def _wait_for_region_applied(frame, region_text, timeout_ms=15000):
    """等待区域选择生效：button#ddm_Area 的文本包含所选区域名（说明 filterArea 已执行）。"""
    if not region_text:
        return
    try:
        await frame.wait_for_function(
            """(text) => {
            const btn = document.querySelector('button#ddm_Area');
            return btn && (btn.innerText || btn.textContent || '').trim().indexOf(text) !== -1;
            }""",
            timeout=timeout_ms,
            arg=region_text.strip(),
        )
    except Exception:
        pass


async def _wait_for_table_refreshed(frame, region_text, wait_after_apply_ms=6000):
    """选择区域后：先等按钮文本更新，再等加载提示消失，再等表格刷新。"""
    await _wait_for_region_applied(frame, region_text, timeout_ms=15000)
    await asyncio.sleep(wait_after_apply_ms / 1000.0)
    try:
        await frame.wait_for_selector("#loadPrompt", state="hidden", timeout=8000)
    except Exception:
        pass
    await _wait_for_table_loaded(frame, min_rows=5)


async def _click_next_page(frame):
    """若 frame 内有分页「下一页」「下页」等，点击并返回 True；否则返回 False。"""
    next_texts = ["下一页", "下页", ">", "next"]
    for text in next_texts:
        try:
            # 优先找可点击的链接或按钮（分页常用 a 或 button）
            loc = frame.get_by_role("link", name=re.compile(re.escape(text), re.I)).first
            await loc.click(timeout=2000)
            await asyncio.sleep(1.5)
            return True
        except Exception:
            pass
        try:
            loc = frame.locator("a, button").filter(has_text=re.compile(re.escape(text), re.I)).first
            await loc.click(timeout=2000)
            await asyncio.sleep(1.5)
            return True
        except Exception:
            pass
    return False


def _is_data_table(rows):
    """过滤掉明显是布局的空表，只保留有数据意义的表格（多行多列，或单行但列多）。"""
    if not rows:
        return False
    min_rows = getattr(config, "MIN_TABLE_ROWS", 2)
    min_cols = getattr(config, "MIN_TABLE_COLS", 2)
    max_cols = max(len(r) for r in rows)
    if len(rows) >= min_rows and max_cols >= min_cols:
        return True
    # 单行但列数较多（≥5）也视为可能的数据行
    if len(rows) == 1 and max_cols >= 5:
        return True
    return False


async def _extract_tables_from_page(page):
    """从当前 page 提取所有 table 的二维数组列表。"""
    tables_data = await page.evaluate(
        """() => {
        const tables = document.querySelectorAll('table');
        return Array.from(tables).map(t => {
            const rows = t.querySelectorAll('tr');
            return Array.from(rows).map(tr => 
                Array.from(tr.querySelectorAll('th, td')).map(cell => (cell.textContent || '').trim())
            );
        });
    }"""
    )
    return tables_data


def _table_extract_js():
    """供 page 或 frame 共用的表格提取 JS。"""
    return """() => {
        const tables = document.querySelectorAll('table');
        return Array.from(tables).map(t => {
            const rows = t.querySelectorAll('tr');
            return Array.from(rows).map(tr =>
                Array.from(tr.querySelectorAll('th, td')).map(cell => (cell.textContent || '').trim())
            );
        });
    }"""


async def _extract_tables_from_frame(frame):
    """从 iframe（Frame 对象）内提取所有 table 的二维数组列表。"""
    try:
        return await frame.evaluate(_table_extract_js())
    except Exception:
        return []


async def _get_region_options(frame):
    """获取 iframe 内两级地域选择器的选项：返回 (level1_options, level2_options, use_custom)。
    先尝试原生 select，若无则用自定义下拉（button#ddm_Area + a.area-item）。"""
    try:
        result = await frame.evaluate(
            """() => {
            const sel = document.querySelectorAll('select');
            if (!sel.length) return { l1: [], l2: [], custom: true };
            const opts = (s) => Array.from(s.options).map(o => (o.textContent || '').trim()).filter(Boolean);
            return {
                l1: opts(sel[0]),
                l2: sel.length > 1 ? opts(sel[1]) : [],
                custom: false
            };
            }"""
        )
        l1 = result.get("l1") or []
        l2 = result.get("l2") or []
        if result.get("custom") is True or (not l1 and not l2):
            l1, l2 = await _get_region_options_custom(frame)
            return (l1, l2, True)
        return (l1, l2, False)
    except Exception:
        pass
    l1, l2 = await _get_region_options_custom(frame)
    return (l1, l2, True)


def _level1_option_selector():
    """一级选项的 CSS 选择器（与 _level1_selector 一致，供 evaluate 用）。"""
    return getattr(config, "REGION_LEVEL1_OPTION_SELECTOR", None) or "ul.dropdown-menu[aria-labelledby='ddm_Area'] > li > a.area-item"


async def _eval_level1_texts(frame, level1_sel=None):
    """在 frame 内用同一选择器取一级选项文本列表（DOM 顺序）。"""
    sel = level1_sel or _level1_option_selector()
    try:
        return await frame.evaluate(
            """(level1Sel) => {
            const items = document.querySelectorAll(level1Sel);
            const texts = [];
            items.forEach(el => {
                const t = (el.textContent || '').trim();
                if (t && t.length > 0 && t.length < 50) texts.push(t);
            });
            return texts;
            }""",
            sel,
        )
    except Exception:
        return []


async def _get_region_options_custom(frame):
    """自定义下拉（button#ddm_Area）：先从 DOM 取一级选项（菜单常在 DOM 中）；若为空再点击触发后取。"""
    level1_sel = _level1_option_selector()
    trigger_sel = getattr(config, "REGION_TRIGGER_SELECTOR", "button#ddm_Area") or "button#ddm_Area"

    try:
        # 先不点击：菜单常在 DOM 中，直接取一级选项
        result = await _eval_level1_texts(frame, level1_sel)
        if isinstance(result, list) and result:
            return (result, [])
        # 若为空则点击按钮展开后再取
        await frame.locator(trigger_sel).first.click(timeout=10000)
        await asyncio.sleep(0.8)
        result = await _eval_level1_texts(frame, level1_sel)
        await frame.keyboard.press("Escape")
        await asyncio.sleep(0.3)
        if isinstance(result, list) and result:
            return (result, [])
    except Exception:
        pass
    return ([], [])


async def _get_region_options_after_open(frame):
    """在「已打开」区域下拉时取一级选项列表，保证顺序与后续按索引点击时一致（避免关闭态与打开态 DOM 顺序不同）。"""
    trigger_sel = getattr(config, "REGION_TRIGGER_SELECTOR", "button#ddm_Area") or "button#ddm_Area"
    level1_sel = _level1_option_selector()
    try:
        await frame.locator(trigger_sel).first.click(timeout=10000)
        await asyncio.sleep(0.8)
        result = await _eval_level1_texts(frame, level1_sel)
        await frame.keyboard.press("Escape")
        await asyncio.sleep(0.3)
        if isinstance(result, list) and result:
            return result
    except Exception:
        pass
    return []


async def _get_level2_options_custom(frame, level1_text, level1_dom_index=None):
    """打开区域下拉后，悬停在一级项（省）上，从子菜单取二级选项（城市）列表。level1_dom_index 不为 None 时按索引悬停，避免 has_text 误匹配。"""
    trigger_sel = getattr(config, "REGION_TRIGGER_SELECTOR", "button#ddm_Area") or "button#ddm_Area"
    sel = _level1_selector()
    try:
        await frame.locator(trigger_sel).first.click()
        await asyncio.sleep(0.5)
        if level1_dom_index is not None:
            level1_link = frame.locator(sel).nth(level1_dom_index)
        else:
            level1_link = frame.locator(sel).filter(has_text=level1_text).first
        await level1_link.hover()
        await asyncio.sleep(0.6)
        # 按索引取子菜单：与悬停的 li 一致
        if level1_dom_index is not None:
            result = await frame.evaluate(
                """(idx) => {
                var mainUl = document.querySelector('ul[aria-labelledby="ddm_Area"]');
                if (!mainUl) return [];
                var items = mainUl.querySelectorAll('li > a.area-item');
                var li = items[idx] ? items[idx].closest('li') : null;
                if (!li || !li.classList.contains('dropdown-submenu')) return [];
                var subUl = li.querySelector('ul.dropdown-menu');
                if (!subUl) return [];
                var links = subUl.querySelectorAll('li > a');
                return Array.from(links).map(function(x) { return (x.textContent || '').trim(); }).filter(Boolean);
                }""",
                level1_dom_index,
            )
        else:
            result = await frame.evaluate(
                """(provinceText) => {
                var mainUl = document.querySelector('ul[aria-labelledby="ddm_Area"]');
                if (!mainUl) return [];
                var items = mainUl.querySelectorAll('li > a.area-item');
                var subUl = null;
                for (var i = 0; i < items.length; i++) {
                    var a = items[i];
                    if ((a.textContent || '').trim() !== provinceText) continue;
                    var li = a.closest('li');
                    if (li && li.classList.contains('dropdown-submenu')) {
                        subUl = li.querySelector('ul.dropdown-menu');
                        break;
                    }
                }
                if (!subUl) return [];
                var links = subUl.querySelectorAll('li > a');
                return Array.from(links).map(function(x) { return (x.textContent || '').trim(); }).filter(Boolean);
                }""",
                level1_text.strip(),
            )
        await frame.keyboard.press("Escape")
        await asyncio.sleep(0.3)
        if isinstance(result, list):
            return result
    except Exception:
        pass
    return []


def _level1_selector():
    """一级区域选项的 locator 选择器（与 _get_region_options_custom 中取选项的 DOM 一致）。"""
    return "ul.dropdown-menu[aria-labelledby='ddm_Area'] > li > a.area-item"


async def _select_region_custom(frame, level1_text, level2_text=None):
    """自定义下拉（#ddm_Area）：打开后选一级；若 level2_text 则悬停一级再点二级（市）。"""
    trigger_sel = getattr(config, "REGION_TRIGGER_SELECTOR", "button#ddm_Area") or "button#ddm_Area"
    wait_ms = getattr(config, "REGION_WAIT_MS", 6000)
    try:
        await frame.locator(trigger_sel).first.click()
        await asyncio.sleep(0.5)
        if level2_text:
            level1_link = frame.locator("ul.dropdown-menu[aria-labelledby='ddm_Area'] > li > a.area-item", has_text=level1_text).first
            await level1_link.hover()
            await asyncio.sleep(0.4)
            city_link = frame.locator("ul.dropdown-menu[aria-labelledby='ddm_Area'] li.dropdown-submenu ul.dropdown-menu li a", has_text=level2_text).first
            await city_link.click()
        else:
            level1_link = frame.locator("ul.dropdown-menu[aria-labelledby='ddm_Area'] > li > a.area-item", has_text=level1_text).first
            await level1_link.click()
        await asyncio.sleep(wait_ms / 1000.0)
    except Exception:
        pass


async def _select_region_custom_by_index(frame, level1_dom_index, level2_text=None):
    """按 DOM 索引选择区域，避免 has_text 误匹配（如“河南”“河北”）。打开下拉后点击第 level1_dom_index 个一级项；若有 level2_text 则悬停该项再点二级。"""
    trigger_sel = getattr(config, "REGION_TRIGGER_SELECTOR", "button#ddm_Area") or "button#ddm_Area"
    wait_ms = getattr(config, "REGION_WAIT_MS", 6000)
    sel = _level1_selector()
    try:
        await frame.locator(trigger_sel).first.click()
        await asyncio.sleep(0.5)
        level1_loc = frame.locator(sel).nth(level1_dom_index)
        if level2_text:
            await level1_loc.hover()
            await asyncio.sleep(0.4)
            city_link = frame.locator("ul.dropdown-menu[aria-labelledby='ddm_Area'] li.dropdown-submenu ul.dropdown-menu li a", has_text=level2_text).first
            await city_link.click()
        else:
            await level1_loc.click()
        await asyncio.sleep(wait_ms / 1000.0)
    except Exception:
        pass


async def _select_region_in_frame(frame, level1_text=None, level2_text=None):
    """在 iframe 内选择一级/二级区域。先尝试原生 select，再尝试自定义下拉（button#ddm_Area）。"""
    wait_ms = getattr(config, "REGION_WAIT_MS", 3000)
    level1_text = level1_text or getattr(config, "REGION_OPTION", "全国")
    try:
        try:
            await frame.locator("select").first.select_option(label=level1_text, timeout=2000)
            await asyncio.sleep(wait_ms / 1000.0)
            if level2_text:
                await frame.locator("select").nth(1).select_option(label=level2_text, timeout=2000)
                await asyncio.sleep(wait_ms / 1000.0)
            return
        except Exception:
            pass
        await _select_region_custom(frame, level1_text=level1_text, level2_text=level2_text)
        return
    except Exception:
        pass
    labels = getattr(config, "REGION_SELECT_LABELS", None) or ["选择区域", "区域"]
    for lab in labels:
        try:
            trigger = frame.get_by_text(lab).first
            await trigger.click(timeout=2000)
            await asyncio.sleep(0.5)
            opt = frame.get_by_text(level1_text).first
            await opt.click(timeout=2000)
            await asyncio.sleep(wait_ms / 1000.0)
            if level2_text:
                opt2 = frame.get_by_text(level2_text).first
                await opt2.click(timeout=2000)
                await asyncio.sleep(wait_ms / 1000.0)
            return
        except Exception:
            continue


def _pick_main_data_table(tables):
    """从合并后的表列表中选出主数据表（行数最多且符合有效表条件）。返回 (header_row, data_rows) 或 (None, [])。"""
    if not tables:
        return (None, [])
    best = None
    best_rows = []
    for t in tables:
        if not t or not _is_data_table(t):
            continue
        if len(t) > len(best_rows):
            best = t
            best_rows = t
    if not best or len(best) < 2:
        return (None, [])
    header = list(best[0])
    max_cols = max(len(r) for r in best)
    data_rows = []
    for r in best[1:]:
        row = list(r) + [""] * (max_cols - len(r))
        row = row[:max_cols]
        data_rows.append(row)
    header = header + [""] * (max_cols - len(header))
    header = header[:max_cols]
    return (header, data_rows)


def _collect_all_data_tables(tables):
    """从所有「表头+数据」表中收集数据并合并为一张表。仅第一个表的首行作为统一表头，
    后续表的每一行（含首行）均为真实数据，全部收集。
    单行表（len==1）在已有 header 时也当作一条数据行收集，避免最后一页「最后一条在单独单行表」被漏掉（偶数条时常见）。"""
    if not tables:
        return (None, [])
    header = None
    max_cols = 0
    all_data_rows = []
    for t in tables:
        if not t:
            continue
        if len(t) < 2:
            # 单行表：若已有 header 且该行像数据行（列数足够），当作最后一条数据收集（解决偶数条漏最后一条）
            if header is not None and len(t) == 1 and len(t[0]) >= getattr(config, "MIN_TABLE_COLS", 2):
                row = list(t[0]) + [""] * (max_cols - len(t[0]))
                row = row[:max_cols]
                all_data_rows.append(row)
            continue
        if not _is_data_table(t):
            continue
        if header is None:
            header = list(t[0])
            max_cols = max(len(r) for r in t)
            rows_to_add = list(t[1:])
        else:
            rows_to_add = list(t)
            max_cols = max(max_cols, max(len(r) for r in t))
        for r in rows_to_add:
            row = list(r) + [""] * (max_cols - len(r))
            row = row[:max_cols]
            all_data_rows.append(row)
    if header is None:
        return (None, [])
    header = header + [""] * (max_cols - len(header))
    header = header[:max_cols]
    all_data_rows = [list(r) + [""] * (max_cols - len(r)) for r in all_data_rows]
    all_data_rows = [r[:max_cols] for r in all_data_rows]
    return (header, all_data_rows)


def _find_column_index(header, candidates):
    """在表头中查找列名（忽略首尾空格），返回第一个匹配的列索引，未找到返回 -1。"""
    if not header:
        return -1
    for i, h in enumerate(header):
        s = (h or "").strip()
        for c in candidates:
            if s == c:
                return i
    return -1


def _region_from_row(row, header, level1_fallback, level2_fallback):
    """根据行数据与表头，取表格中的省份/城市作为一级/二级区域；若无则用 fallback。"""
    province_idx = _find_column_index(header, ["省份"])
    city_idx = _find_column_index(header, ["城市", "地市", "地区", "地市名称"])
    level1 = level1_fallback
    level2 = level2_fallback
    if province_idx >= 0 and province_idx < len(row) and row[province_idx] and str(row[province_idx]).strip():
        level1 = str(row[province_idx]).strip()
    if city_idx >= 0 and city_idx < len(row) and row[city_idx] and str(row[city_idx]).strip():
        level2 = str(row[city_idx]).strip()
    return (level1, level2)


def _merge_header_data_tables(tables):
    """若前一个表仅 1 行且与后一个表列数一致，则合并为 [表头行] + [数据行]，返回合并后的表列表（去重/去空）。"""
    if not tables or len(tables) < 2:
        return tables
    merged = []
    i = 0
    while i < len(tables):
        t = tables[i]
        if not t:
            i += 1
            continue
        if i + 1 < len(tables) and len(t) == 1:
            next_t = tables[i + 1]
            if next_t:
                cols_head = len(t[0])
                cols_body = max(len(r) for r in next_t) if next_t else 0
                if cols_head == cols_body or cols_body <= cols_head + 2:
                    merged.append(t + next_t)
                    i += 2
                    continue
        merged.append(t)
        i += 1
    return merged


async def _scrape_frame_all_pages(frame):
    """从当前 frame 抓取表格数据（含分页），返回 (header, data_rows)。"""
    tables_raw = await _extract_tables_from_frame(frame)
    tables_merged = _merge_header_data_tables(tables_raw or [])
    header, data_rows = _collect_all_data_tables(tables_merged)
    if not header or not data_rows:
        return (None, [])
    all_rows = list(data_rows)
    while await _click_next_page(frame):
        # 翻页后先等加载提示消失，再等表格；最后一页可能只有 1 行，用 min_rows=1 避免漏抓
        try:
            await frame.wait_for_selector("#loadPrompt", state="hidden", timeout=8000)
        except Exception:
            pass
        await _wait_for_table_loaded(frame, min_rows=1)
        tables_raw = await _extract_tables_from_frame(frame)
        tables_merged = _merge_header_data_tables(tables_raw or [])
        _, more_rows = _collect_all_data_tables(tables_merged)
        # 若翻页后拿到 0 行，可能是新页尚未渲染，再等一次并重试抓取一次（避免漏掉最后一页仅 1 条）
        if not more_rows:
            await asyncio.sleep(2.0)
            tables_raw = await _extract_tables_from_frame(frame)
            tables_merged = _merge_header_data_tables(tables_raw or [])
            _, more_rows = _collect_all_data_tables(tables_merged)
        if more_rows:
            all_rows.extend(more_rows)
    return (header, all_rows)


async def _get_data_frame(page):
    """定位数据 iframe（RealDatas.html）。先按 url 匹配，再按 iframe#MF 取 content_frame。"""
    wait_ms = getattr(config, "IFRAME_WAIT_MS", 8000)
    src_key = getattr(config, "DATA_IFRAME_SRC_CONTAINS", "RealDatas") or "RealDatas"
    iframe_id = getattr(config, "DATA_IFRAME_ID", "MF") or "MF"
    await asyncio.sleep(wait_ms / 1000.0)
    for frame in page.frames:
        if frame == page.main_frame:
            continue
        if src_key in (frame.url or ""):
            # 可选：等待 frame 内出现表格（AJAX 可能延后）
            try:
                await frame.wait_for_selector("table", timeout=3000)
            except Exception:
                pass
            return frame
    try:
        el = await page.query_selector(f"iframe#{iframe_id}")
        if el:
            return await el.content_frame()
    except Exception:
        pass
    return None


async def _extract_text_from_page(page, max_chars=10000):
    """提取当前页面主要文本（用于无表格时的内容）。"""
    return await page.evaluate(
        """(maxChars) => {
        const body = document.body;
        if (!body) return '';
        let text = (body.innerText || '').trim();
        return text.length > maxChars ? text.slice(0, maxChars) + '...' : text;
    }"""
    )


async def _click_and_collect(context, page, url, sheet_name, collected_urls, all_sheets):
    """在 page 上点击入口并收集表格；若为同源链接则递归。"""
    base_url = config.BASE_URL
    base_domain = urlparse(base_url).netloc

    # 当前页表格（只保留有效表格）
    tables = await _extract_tables_from_page(page)
    if tables:
        for i, rows in enumerate(tables):
            if rows and _is_data_table(rows):
                all_sheets.append({"sheet_name": f"{sheet_name}_表格{i+1}", "rows": rows})
    else:
        text = await _extract_text_from_page(page)
        if text.strip():
            lines = [line.strip() for line in text.split("\n") if line.strip()][:500]
            if lines:
                all_sheets.append({"sheet_name": sheet_name, "rows": [["内容"], *[[ln] for ln in lines]]})

    # 同页面上的链接（仅同源且未访问过）
    links = await page.evaluate(
        """() => {
        const as = Array.from(document.querySelectorAll('a[href]'));
        return as.map(a => ({
            text: (a.textContent || '').trim().slice(0, 80),
            href: a.getAttribute('href')
        }));
    }"""
    )
    for link in links:
        href = link.get("href")
        text = (link.get("text") or "").strip()
        full_url = _normalize_href(url, href)
        if not full_url or full_url in collected_urls:
            continue
        if not _same_origin(base_url, full_url):
            continue
        collected_urls.add(full_url)
        try:
            new_page = await context.new_page()
            new_page.set_default_timeout(config.TIMEOUT_MS)
            await new_page.goto(full_url, wait_until="domcontentloaded")
            await asyncio.sleep(config.CLICK_WAIT_MS / 1000.0)
            name = re.sub(r"[^\w\u4e00-\u9fff\s-]", "", text)[:30] or "页面"
            await _click_and_collect(context, new_page, full_url, name, collected_urls, all_sheets)
            await new_page.close()
        except Exception as e:
            print(f"  访问子链接失败 {full_url}: {e}")
            try:
                await new_page.close()
            except Exception:
                pass


async def run_crawl():
    """主抓取流程：打开主页 -> 优先点击「实时数据」「发布说明」-> 遍历同源链接 -> 提取表格 -> 返回 sheets 数据。"""
    all_sheets = []
    collected_urls = set()
    base_url = config.BASE_URL

    async with async_playwright() as p:
        launch_opts = {"headless": config.HEADLESS}
        if getattr(config, "CHROMIUM_EXECUTABLE_PATH", None):
            launch_opts["executable_path"] = config.CHROMIUM_EXECUTABLE_PATH
        browser = await p.chromium.launch(**launch_opts)
        context = await browser.new_context(
            ignore_https_errors=config.IGNORE_HTTPS_ERRORS,
            viewport={"width": 1280, "height": 800},
            user_agent=getattr(config, "USER_AGENT", None) or None,
            extra_http_headers=getattr(config, "EXTRA_HTTP_HEADERS", None) or {},
        )
        page = await context.new_page()
        page.set_default_timeout(config.TIMEOUT_MS)

        try:
            await page.goto(base_url, wait_until="domcontentloaded")
            await asyncio.sleep(config.NAVIGATION_WAIT_MS / 1000.0)
            collected_urls.add(base_url.rstrip("/"))

            # 先处理主页面上的表格/内容（只保留有效表格）
            tables = await _extract_tables_from_page(page)
            if tables:
                for i, rows in enumerate(tables):
                    if rows and _is_data_table(rows):
                        all_sheets.append({"sheet_name": f"主页_表格{i+1}", "rows": rows})
            else:
                text = await _extract_text_from_page(page)
                if text.strip():
                    lines = [line.strip() for line in text.split("\n") if line.strip()][:500]
                    if lines:
                        all_sheets.append({"sheet_name": "主页", "rows": [["内容"], *[[ln] for ln in lines]]})

            # 定位并处理数据 iframe（RealDatas.html）：单 sheet 汇总 + 一级/二级区域列
            data_frame = await _get_data_frame(page)
            run_mode = getattr(config, "RUN_MODE", "test").lower()
            single_sheet_name = getattr(config, "SINGLE_SHEET_NAME", "水质实时数据")
            col1 = getattr(config, "REGION_COLUMN_1", "一级区域")
            col2 = getattr(config, "REGION_COLUMN_2", "二级区域")
            prod_interval_ms = getattr(config, "PROD_INTERVAL_MS", 5000)

            unified_header = None
            unified_rows = []

            if not data_frame:
                print("  [iframe] 未找到数据 frame（RealDatas），请检查页面或延长 IFRAME_WAIT_MS")
            else:
                print("  [iframe] 数据 frame 已找到，RUN_MODE=%s" % run_mode)

                async def _scrape_and_append(level1_text, level2_text):
                    """当前已选好区域，抓表并给每行加上一级/二级区域，追加到 unified_*。
                    若表头含「省份」则用该行「省份」作为一级区域；若含「城市/地市/地区」则作为二级区域，保证与数据一致。"""
                    nonlocal unified_header, unified_rows
                    tables_raw = await _extract_tables_from_frame(data_frame)
                    tables_merged = _merge_header_data_tables(tables_raw or [])
                    header, data_rows = _collect_all_data_tables(tables_merged)
                    if not header or not data_rows:
                        return 0
                    if unified_header is None:
                        unified_header = header + [col1, col2]
                    for row in data_rows:
                        r1, r2 = _region_from_row(row, header, level1_text, level2_text)
                        unified_rows.append(row + [r1, r2])
                    return len(data_rows)

                if run_mode == "prod":
                    # 第一步：获取地域选择器中所有省份（去掉「全国」），保存成列表
                    await asyncio.sleep(2.0)
                    try:
                        await data_frame.wait_for_selector("button#ddm_Area", state="visible", timeout=10000)
                    except Exception:
                        pass
                    province_list_raw = await _get_region_options_after_open(data_frame)
                    nationwide = getattr(config, "REGION_OPTION", "全国")
                    province_list = [p for p in (province_list_raw or []) if p != nationwide]
                    if not province_list:
                        province_list_raw, _, _ = await _get_region_options(data_frame)
                        province_list = [p for p in (province_list_raw or []) if p != nationwide]
                    if not province_list:
                        print("  [iframe] prod 未检测到省份列表，按全国抓取一次")
                        await _select_region_in_frame(data_frame, level1_text=config.REGION_OPTION, level2_text=None)
                        await _wait_for_table_loaded(data_frame, min_rows=5)
                        header, rows = await _scrape_frame_all_pages(data_frame)
                        if header and rows:
                            all_sheets.append({
                                "sheet_name": single_sheet_name,
                                "rows": [header] + rows,
                                "province": config.REGION_OPTION,
                            })
                    else:
                        print("  [iframe] prod 已获取省份列表，共 %d 个（已去掉「全国」）" % len(province_list))
                        top_n = getattr(config, "PROD_TOP_N", None)
                        if top_n and top_n > 0:
                            province_list = province_list[: int(top_n)]
                            print("  [iframe] prod 仅抓取前 %d 个省份（PROD_TOP_N=%s）" % (len(province_list), top_n))
                        # 第二步：依次刷新页面 → 选择指定省份 → 抓取数据 → 每个省份独立一项（由 main 保存成独立文件）
                        for province in province_list:
                            await page.goto(base_url, wait_until="domcontentloaded")
                            await asyncio.sleep(config.NAVIGATION_WAIT_MS / 1000.0)
                            data_frame = await _get_data_frame(page)
                            if not data_frame:
                                print("  [iframe] 刷新后未找到数据 frame，跳过 %s" % province)
                                continue
                            try:
                                await data_frame.wait_for_selector("button#ddm_Area", state="visible", timeout=10000)
                            except Exception:
                                pass
                            await _select_region_custom(data_frame, level1_text=province, level2_text=None)
                            await _wait_for_table_refreshed(data_frame, province, wait_after_apply_ms=config.REGION_WAIT_MS)
                            header, rows = await _scrape_frame_all_pages(data_frame)
                            if header and rows:
                                all_sheets.append({
                                    "sheet_name": single_sheet_name,
                                    "rows": [header] + rows,
                                    "province": province,
                                })
                                print("  [iframe] 区域 %s: %d 行" % (province, len(rows)))
                            else:
                                print("  [iframe] 区域 %s: 无数据" % province)
                            await asyncio.sleep(prod_interval_ms / 1000.0)
                            await _random_wait()
                else:
                    # test：仅抓「全国」，等待表格加载完成后再抓
                    level1 = getattr(config, "REGION_OPTION", "全国")
                    await _select_region_in_frame(data_frame, level1_text=level1, level2_text=None)
                    await asyncio.sleep(config.REGION_WAIT_MS / 1000.0)
                    l1_opts, l2_opts, _ = await _get_region_options(data_frame)
                    level2 = level1
                    if l2_opts:
                        try:
                            await data_frame.locator("select").nth(1).select_option(label=l2_opts[0], timeout=3000)
                            await asyncio.sleep(config.REGION_WAIT_MS / 1000.0)
                            level2 = l2_opts[0]
                        except Exception:
                            try:
                                await data_frame.locator("select").nth(1).select_option(index=0, timeout=3000)
                                await asyncio.sleep(config.REGION_WAIT_MS / 1000.0)
                                level2 = l2_opts[0]
                            except Exception:
                                pass
                    await _wait_for_table_loaded(data_frame, min_rows=10)
                    n = await _scrape_and_append(level1, level2)
                    if n:
                        print("  [iframe] 全国: %d 行" % n)

                if unified_header and unified_rows:
                    all_sheets.append({
                        "sheet_name": single_sheet_name,
                        "rows": [unified_header] + unified_rows,
                    })
                    print("  [iframe] 已汇总到单 sheet「%s」: 共 %d 行" % (single_sheet_name, len(unified_rows)))

            # 优先点击主页面「实时数据」「发布说明」等（可能切换 iframe 或跳转）
            for link_text in config.PRIORITY_LINK_TEXTS:
                try:
                    loc = page.get_by_role("link", name=re.compile(re.escape(link_text), re.I))
                    await loc.first.click(timeout=1500)
                    await asyncio.sleep(config.CLICK_WAIT_MS / 1000.0)
                    tables = await _extract_tables_from_page(page)
                    if tables:
                        for i, rows in enumerate(tables):
                            if rows and _is_data_table(rows):
                                all_sheets.append({"sheet_name": f"{link_text}_表格{i+1}", "rows": rows})
                    else:
                        text = await _extract_text_from_page(page)
                        if text.strip():
                            lines = [line.strip() for line in text.split("\n") if line.strip()][:500]
                            if lines:
                                all_sheets.append({"sheet_name": link_text, "rows": [["内容"], *[[ln] for ln in lines]]})
                except (PlaywrightTimeout, Exception) as e:
                    print(f"  未找到或点击「{link_text}」: {e}")

            # 回到主页面，再收集所有同源链接并逐个访问
            await page.goto(base_url, wait_until="domcontentloaded")
            await asyncio.sleep(config.CLICK_WAIT_MS / 1000.0)

            # 收集当前页所有同源链接并逐个访问
            links = await page.evaluate(
                """() => {
                const as = Array.from(document.querySelectorAll('a[href]'));
                return as.map(a => ({
                    text: (a.textContent || '').trim().slice(0, 80),
                    href: a.getAttribute('href')
                }));
            }"""
            )
            for link in links:
                href = link.get("href")
                text = (link.get("text") or "").strip()
                full_url = _normalize_href(base_url, href)
                if not full_url or full_url in collected_urls:
                    continue
                if not _same_origin(base_url, full_url):
                    continue
                collected_urls.add(full_url)
                try:
                    new_page = await context.new_page()
                    new_page.set_default_timeout(config.TIMEOUT_MS)
                    await new_page.goto(full_url, wait_until="domcontentloaded")
                    await asyncio.sleep(config.CLICK_WAIT_MS / 1000.0)
                    name = re.sub(r"[^\w\u4e00-\u9fff\s-]", "", text)[:30] or "子页面"
                    await _click_and_collect(context, new_page, full_url, name, collected_urls, all_sheets)
                    await new_page.close()
                except Exception as e:
                    print(f"  访问子链接失败 {full_url}: {e}")
                    try:
                        await new_page.close()
                    except Exception:
                        pass

        except Exception as e:
            print(f"抓取过程出错: {e}")
            raise
        finally:
            await browser.close()

    return all_sheets


def run():
    """同步入口：执行异步抓取并返回 sheets 数据。"""
    return asyncio.run(run_crawl())


if __name__ == "__main__":
    data = run()
    print(f"共抓取到 {len(data)} 个数据块（表格/文本页）")
    for s in data:
        rows_count = len(s.get("rows", []))
        print(f"  - {s.get('sheet_name')}: {rows_count} 行")
