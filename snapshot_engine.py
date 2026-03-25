"""
snapshot_engine.py
Parallel browser screenshot engine + Excel builder.
"""

import asyncio
import uuid
import os
import io
import platform
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image as PILImage, ImageDraw, ImageFont
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
from openpyxl.drawing.image import Image as XLImage

BASE_DIR   = Path(__file__).parent
PROFILE    = BASE_DIR / "browser_profile"
OUTPUTS    = BASE_DIR / "outputs"
OUTPUTS.mkdir(exist_ok=True)
PROFILE.mkdir(exist_ok=True)

# Force headless on Linux with no display (Railway/Docker), or if HEADLESS=true is set
_on_linux_no_display = (platform.system() == "Linux" and not os.environ.get("DISPLAY"))
HEADLESS = _on_linux_no_display or os.environ.get("HEADLESS", "false").lower() == "true"

# ── shared status dict (read by Flask for live polling) ─────────────────────
status = {
    "running"     : False,
    "message"     : "Idle — ready to run.",
    "last_run"    : None,
    "last_output" : None,
    "last_file"   : None,
    "error"       : None,
    "history"     : [],          # list of {ts, file, pages}
}

# ── helpers ──────────────────────────────────────────────────────────────────
def _fill(hex_): return PatternFill("solid", start_color=hex_, end_color=hex_)
def _border(color="CCCCCC"):
    s = Side(style="thin", color=color)
    return Border(left=s, right=s, top=s, bottom=s)

CHAIN_COLORS = {
    "TON"      : "0098EA",
    "SUI"      : "4DA2FF",
    "TRON"     : "E53935",
    "ETH"      : "627EEA",
    "SOL"      : "9945FF",
    "BTC"      : "F7931A",
    "BNB"      : "F3BA2F",
    "ALGO"     : "00BCD4",
    "SEI"      : "9C27B0",
    "STELLAR"  : "7986CB",
    "XDC"      : "1976D2",
    "BERACHAIN": "F4811F",
}
DEFAULT_CC = "6C63FF"
NAVY="1A1A2E"; WHITE="FFFFFF"; LGRAY="F5F6FA"

# ── timestamp bar stamper ─────────────────────────────────────────────────────
def _crop_height(screenshot_bytes, max_height):
    """Crop screenshot to max_height pixels (0 = no limit)."""
    if not max_height:
        return screenshot_bytes
    try:
        img = PILImage.open(io.BytesIO(screenshot_bytes))
        if img.height <= max_height:
            return screenshot_bytes
        cropped = img.crop((0, 0, img.width, max_height))
        buf = io.BytesIO()
        cropped.save(buf, format="PNG")
        return buf.getvalue()
    except Exception:
        return screenshot_bytes

def _stamp_bar(screenshot_bytes, chain, token, explorer_name, url, timestamp):
    """Burn a dark audit bar onto the bottom of a screenshot PNG."""
    try:
        img  = PILImage.open(io.BytesIO(screenshot_bytes)).convert("RGB")
        bar_h = 36
        out  = PILImage.new("RGB", (img.width, img.height + bar_h), (10, 17, 34))
        out.paste(img, (0, 0))
        draw = ImageDraw.Draw(out)

        # dark bar background
        draw.rectangle([0, img.height, img.width, img.height + bar_h],
                       fill=(10, 17, 34))
        # subtle top border line
        draw.line([0, img.height, img.width, img.height], fill=(30, 50, 80), width=1)

        # try Windows fonts, fall back to PIL default
        font_paths = [
            "C:/Windows/Fonts/consola.ttf",
            "C:/Windows/Fonts/cour.ttf",
            "C:/Windows/Fonts/arial.ttf",
        ]
        font = None
        for fp in font_paths:
            try:
                font = ImageFont.truetype(fp, 14)
                break
            except Exception:
                pass
        if font is None:
            font = ImageFont.load_default()

        left  = f"AUDIT CAPTURE  —  {chain}  |  {explorer_name}  ·  {url}"
        right = timestamp

        y = img.height + (bar_h - 16) // 2
        draw.text((12, y), left,  fill=(52, 211, 153), font=font)   # green

        # right-align timestamp
        try:
            tw = draw.textlength(right, font=font)
        except AttributeError:
            tw = font.getlength(right)
        draw.text((img.width - tw - 12, y), right, fill=(148, 163, 184), font=font)

        buf = io.BytesIO()
        out.save(buf, format="PNG")
        return buf.getvalue()
    except Exception:
        return screenshot_bytes   # if anything fails, return original untouched

# ── async capture ─────────────────────────────────────────────────────────────
async def _capture(wallets, cb, wait_secs=12, max_height=3000):
    """Open every explorer URL in parallel, wait, screenshot all at once."""
    from playwright.async_api import async_playwright

    tasks = []
    for w in wallets:
        addr = w.get("address", "")
        for exp in w.get("explorers", []):
            url = exp.get("url", "").replace("{address}", addr)
            if url:
                tasks.append({"wallet": w, "explorer": exp, "url": url})

    if not tasks:
        return []

    cb(f"Opening {len(tasks)} pages simultaneously…")
    results = []

    async with async_playwright() as p:
        ctx = await p.chromium.launch_persistent_context(
            user_data_dir=str(PROFILE),
            headless=HEADLESS,
            viewport={"width": 1440, "height": 820},
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-infobars",
                "--no-sandbox",                  # required on Linux servers
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",       # prevents crashes in Docker
            ],
        )

        # open one page per task
        pages = []
        for t in tasks:
            pg = await ctx.new_page()
            pages.append((pg, t))

        # navigate ALL simultaneously
        cb("Navigating all pages in parallel…")
        await asyncio.gather(
            *[pg.goto(t["url"], wait_until="domcontentloaded", timeout=30000)
              for pg, t in pages],
            return_exceptions=True,
        )

        cb(f"Waiting {wait_secs}s for dynamic content to render…")
        await asyncio.sleep(wait_secs)

        # for viewport screenshots, scroll to top so the address header is visible
        cb("Preparing pages for capture…")
        async def _scroll_top(pg, full_page):
            try:
                if not full_page:
                    await pg.evaluate("window.scrollTo(0, 0);")
                    await asyncio.sleep(0.3)
            except Exception:
                pass

        await asyncio.gather(
            *[_scroll_top(pg, t["explorer"].get("full_page", True))
              for pg, t in pages],
            return_exceptions=True,
        )

        cb("Taking screenshots…")
        ts  = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        sss = await asyncio.gather(
            *[pg.screenshot(full_page=t["explorer"].get("full_page", True))
              for pg, t in pages],
            return_exceptions=True,
        )

        for (pg, t), ss in zip(pages, sss):
            if isinstance(ss, Exception):
                stamped = None
                err = str(ss)
            else:
                # crop full-page screenshots to avoid capturing endless footers
                if t["explorer"].get("full_page", True) and max_height:
                    ss = _crop_height(ss, max_height)
                stamped = _stamp_bar(
                    ss,
                    chain        = t["wallet"].get("chain", ""),
                    token        = t["wallet"].get("token", ""),
                    explorer_name= t["explorer"].get("name", ""),
                    url          = t["url"],
                    timestamp    = ts,
                )
                err = None
            results.append({
                "wallet"    : t["wallet"],
                "explorer"  : t["explorer"],
                "url"       : t["url"],
                "timestamp" : ts,
                "screenshot": stamped,
                "error"     : err,
            })

        await ctx.close()

    return results


# ── Excel builder ─────────────────────────────────────────────────────────────
def _build_excel(results, ts_str):
    wb = Workbook()
    wb.remove(wb.active)

    # group by chain
    chains = {}
    for r in results:
        c = r["wallet"].get("chain", "Other")
        chains.setdefault(c, []).append(r)

    # ── Summary ──────────────────────────────────────────────────────────────
    ws = wb.create_sheet("Summary")
    ws.sheet_view.showGridLines = False
    for col, w in zip("ABCDEFG", [4,12,18,48,16,10,26]):
        ws.column_dimensions[col].width = w

    ws.merge_cells("A1:G1")
    c = ws["A1"]
    c.value  = "WALLET BALANCE SNAPSHOT"
    c.font   = Font(name="Arial", bold=True, size=16, color=WHITE)
    c.fill   = _fill(NAVY)
    c.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[1].height = 38

    ws.merge_cells("A2:G2")
    c = ws["A2"]
    c.value = (f"Captured: {ts_str}   |   "
               f"{len(results)} pages   |   {len(chains)} chain(s)")
    c.font  = Font(name="Arial", size=10, color="8892B0", italic=True)
    c.fill  = _fill(NAVY)
    c.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[2].height = 20
    ws.merge_cells("A3:G3"); ws["A3"].fill = _fill(NAVY)
    ws.row_dimensions[3].height = 6

    for i, h in enumerate(["","Chain","Explorer","Address","Token","OK?","Captured"],1):
        c = ws.cell(row=4, column=i, value=h)
        c.font  = Font(name="Arial", bold=True, size=10, color=WHITE)
        c.fill  = _fill("2D3561")
        c.alignment = Alignment(horizontal="center", vertical="center")
        c.border = _border(WHITE)
    ws.row_dimensions[4].height = 20

    for ri, r in enumerate(results, 5):
        chain = r["wallet"].get("chain","?")
        cc    = CHAIN_COLORS.get(chain, DEFAULT_CC)
        bg    = LGRAY if ri % 2 == 0 else WHITE
        ws.cell(row=ri, column=1).fill   = _fill(cc)
        ws.cell(row=ri, column=1).border = _border()
        ok = "✓" if r["screenshot"] else "✗"
        for ci, v in enumerate([chain,
                                 r["explorer"].get("name",""),
                                 r["wallet"].get("address",""),
                                 r["wallet"].get("token",""),
                                 ok, r["timestamp"]], 2):
            c = ws.cell(row=ri, column=ci, value=v)
            c.font = Font(name="Arial", size=10,
                          color="00873E" if v=="✓" else ("CC0000" if v=="✗" else "333333"))
            c.fill = _fill(bg)
            c.alignment = Alignment(horizontal="left", vertical="center")
            c.border = _border()
        ws.row_dimensions[ri].height = 18

    # ── One tab per chain ─────────────────────────────────────────────────────
    tmp_files = []   # collect temp PNGs; delete AFTER wb.save()
    for chain_name, chain_results in chains.items():
        cc = CHAIN_COLORS.get(chain_name, DEFAULT_CC)
        ws = wb.create_sheet(chain_name)
        ws.sheet_view.showGridLines = False
        for col, w in zip("ABCDEF", [3,22,52,18,18,3]):
            ws.column_dimensions[col].width = w

        ws.merge_cells("A1:F1")
        c = ws["A1"]
        c.value = f"  {chain_name}  —  Snapshot   |   {ts_str}"
        c.font  = Font(name="Arial", bold=True, size=14, color=WHITE)
        c.fill  = _fill(cc)
        c.alignment = Alignment(horizontal="left", vertical="center")
        ws.row_dimensions[1].height = 34

        row = 3
        for r in chain_results:
            w_info = r["wallet"]; exp = r["explorer"]

            # explorer header bar
            ws.merge_cells(start_row=row,start_column=2,end_row=row,end_column=5)
            c = ws.cell(row=row, column=2,
                        value=f"  {exp.get('name','')}   ·   {r['url']}")
            c.font  = Font(name="Arial", bold=True, size=11, color=WHITE)
            c.fill  = _fill(cc)
            c.alignment = Alignment(horizontal="left", vertical="center")
            ws.row_dimensions[row].height = 24; row += 1

            # metadata rows
            for label, val in [
                ("Address" , w_info.get("address","")),
                ("Token"   , w_info.get("token","")),
                ("Captured", r["timestamp"]),
            ]:
                cl = ws.cell(row=row, column=2, value=label)
                cl.font = Font(name="Arial", bold=True, size=10, color="555555")
                cl.fill = _fill(LGRAY); cl.border = _border()
                cl.alignment = Alignment(horizontal="left", vertical="center")
                ws.merge_cells(start_row=row,start_column=3,end_row=row,end_column=5)
                cv = ws.cell(row=row, column=3, value=val)
                cv.font = Font(name="Arial", size=10, color=NAVY)
                cv.fill = _fill(WHITE); cv.border = _border()
                cv.alignment = Alignment(horizontal="left", vertical="center")
                ws.row_dimensions[row].height = 18; row += 1

            row += 1   # spacer

            # embed screenshot
            if r["screenshot"]:
                tmp = BASE_DIR / f"_tmp_{uuid.uuid4().hex}.png"
                tmp.write_bytes(r["screenshot"])
                tmp_files.append(tmp)          # delete after save, not now
                img = XLImage(str(tmp))
                # maintain aspect ratio — fit to 900px wide, scale height proportionally
                raw = PILImage.open(io.BytesIO(r["screenshot"]))
                ratio = raw.height / raw.width if raw.width else 1
                img.width  = 900
                img.height = int(900 * ratio)
                ws.add_image(img, f"B{row}")
                # each Excel row ≈ 20pt tall; calculate how many rows the image needs
                n_rows = max(1, int(img.height / 20) + 1)
                for r2 in range(row, row + n_rows):
                    ws.row_dimensions[r2].height = 20
                row += n_rows + 1
            else:
                c = ws.cell(row=row, column=2,
                            value=f"⚠  Screenshot failed: {r.get('error','')}")
                c.font = Font(name="Arial", size=10, color="CC0000")
                row += 2

            row += 2   # gap between explorers

    # save — temp PNGs must still exist at this point
    safe_ts = ts_str.replace(" ","_").replace(":","").replace("/","-")
    out = OUTPUTS / f"Snapshot_{safe_ts}.xlsx"
    wb.save(str(out))

    # now safe to delete temp files
    for tmp in tmp_files:
        try: tmp.unlink()
        except: pass

    return str(out)


# ── public synchronous entry point ────────────────────────────────────────────
def run_snapshot(wallets, wait_secs=12, max_height=3000):
    """Called from a background thread by Flask."""
    global status
    status.update(running=True, error=None, message="Starting…")

    def cb(msg):
        status["message"] = msg

    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        results  = loop.run_until_complete(_capture(wallets, cb, wait_secs=wait_secs, max_height=max_height))
        loop.close()

        cb("Building Excel report…")
        ts_str   = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
        out_path = _build_excel(results, ts_str)
        fname    = Path(out_path).name

        status.update(
            running=False, last_run=ts_str,
            last_output=out_path, last_file=fname,
            message=f"Done ✓  →  {fname}",
        )
        status["history"].insert(0, {"ts": ts_str, "file": fname,
                                     "pages": len(results)})
        status["history"] = status["history"][:20]   # keep last 20
        return out_path

    except Exception as e:
        status.update(running=False, error=str(e),
                      message=f"Error: {e}")
        raise
