# -*- coding: utf-8 -*-
"""
页面结构分析脚本：打开目标页面，枚举所有可点击元素与表格，
将结果保存到 output/page_structure.json，便于制定/调整抓取计划。
"""
import asyncio
import json
import os
from pathlib import Path

try:
    from playwright.async_api import async_playwright
except ImportError:
    print("请先安装: pip install playwright && playwright install chromium")
    raise

import config


async def analyze():
    result = {
        "url": config.BASE_URL,
        "links": [],
        "buttons": [],
        "tables": [],
        "iframes": [],
        "raw_html_summary": None,
    }
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
            resp = await page.goto(config.BASE_URL, wait_until="domcontentloaded")
            if resp and resp.status >= 400:
                result["error"] = f"HTTP {resp.status} at {config.BASE_URL}"
                result["http_status"] = resp.status
            if result.get("error"):
                out_dir = Path(config.OUTPUT_DIR)
                out_dir.mkdir(parents=True, exist_ok=True)
                out_file = out_dir / "page_structure.json"
                with open(out_file, "w", encoding="utf-8") as f:
                    json.dump(result, f, ensure_ascii=False, indent=2)
                print(f"页面加载失败，结果已保存到: {out_file}")
                print(f"  错误: {result['error']}")
                await browser.close()
                return result
            await asyncio.sleep(config.NAVIGATION_WAIT_MS / 1000.0)

            # 收集所有链接
            links = await page.evaluate(
                """() => {
                const as = Array.from(document.querySelectorAll('a[href]'));
                return as.map(a => ({
                    text: (a.textContent || '').trim().slice(0, 100),
                    href: a.getAttribute('href'),
                    id: a.id || null,
                    className: a.className || null
                }));
            }"""
            )
            result["links"] = links

            # 收集所有按钮及可点击元素
            buttons = await page.evaluate(
                """() => {
                const els = Array.from(document.querySelectorAll('button, [onclick], input[type="submit"], [role="button"]'));
                return els.map(el => ({
                    tag: el.tagName,
                    text: (el.textContent || el.value || '').trim().slice(0, 100),
                    onclick: el.getAttribute('onclick') || null,
                    id: el.id || null,
                    className: el.className || null
                }));
            }"""
            )
            result["buttons"] = buttons

            # 收集表格数量与表头摘要
            tables = await page.evaluate(
                """() => {
                const tables = Array.from(document.querySelectorAll('table'));
                return tables.map((t, i) => {
                    const rows = t.querySelectorAll('tr');
                    const firstRow = rows[0] ? Array.from(rows[0].querySelectorAll('th, td')).map(c => c.textContent.trim().slice(0, 50)) : [];
                    return { index: i, rowCount: rows.length, firstRowCells: firstRow };
                });
            }"""
            )
            result["tables"] = tables

            # iframe
            iframes = await page.evaluate(
                """() => {
                const ifs = Array.from(document.querySelectorAll('iframe'));
                return ifs.map((f, i) => ({ index: i, src: f.getAttribute('src'), id: f.id || null }));
            }"""
            )
            result["iframes"] = iframes

            # 页面标题与部分 body 文本（便于确认是否为主页）
            result["title"] = await page.title()
            result["body_text_sample"] = await page.evaluate(
                """() => (document.body && document.body.innerText || '').slice(0, 500)"""
            )

        except Exception as e:
            result["error"] = str(e)
            if "ERR_EMPTY_RESPONSE" in str(e):
                result["hint"] = (
                    "ERR_EMPTY_RESPONSE 通常表示当前网络无法访问该地址、或服务器对请求返回空。"
                    "可尝试：在能正常打开该页面的本机/网络下运行；或检查防火墙/代理。"
                )
        finally:
            await browser.close()

    out_dir = Path(config.OUTPUT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / "page_structure.json"
    with open(out_file, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"页面结构已保存到: {out_file}")
    if result.get("hint"):
        print(f"  说明: {result['hint']}")
    return result


def main():
    os.chdir(Path(__file__).resolve().parent)
    asyncio.run(analyze())


if __name__ == "__main__":
    main()
