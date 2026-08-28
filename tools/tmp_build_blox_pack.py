#!/usr/bin/env python3
import csv
import io
import json
import re
import time
import zipfile
import hashlib
import unicodedata
from collections import defaultdict
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from PIL import Image, ImageOps

BASE = "https://bloxfruitswiki.org/wiki/"
OUT = Path("build/blox_fruits_emoji_pack")
FINAL_ZIP = Path("build/Blox_Fruits_Emoji_Pack_COMPLETO.zip")
SIZE = 256
SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131 Safari/537.36 BloxFruitsEmojiPack/1.0",
    "Accept-Language": "en-US,en;q=0.9",
})

# The archive mirrors Blox Fruits Wiki and is crawled frequently.  The script
# intentionally stores source URLs in the manifest so every icon is traceable.
PAGES = {
    "01_Frutas_Permanentes": ["permanent-blox-fruits", "blox-fruits"],
    "02_Frutas_Fisicas": ["blox-fruits"],
    "03_Skins/01_Icones": ["skins", "aura/skins", "dragon/skins", "empyrean/skins", "pain/skins", "lightning/skins", "portal/skins", "diamond/skins", "eagle/skins", "bomb/skins"],
    "03_Skins/02_Fisicas": ["skins", "dragon/skins", "empyrean/skins", "pain/skins", "lightning/skins", "portal/skins", "diamond/skins", "eagle/skins", "bomb/skins"],
    "04_Mutacoes/01_Icones": ["mutations", "empyrean", "fiend", "werewolf"],
    "04_Mutacoes/02_Fisicas": ["mutations", "empyrean", "fiend", "werewolf"],
    "05_Racas": ["races", "human", "rabbit", "shark", "angel", "ghoul", "cyborg", "draco"],
    "06_Espadas": ["swords"],
    "07_Armas": ["guns"],
    "08_Acessorios": ["accessories"],
    "09_Trinkets": ["trinkets"],
    "10_Gear": ["gears", "fishing-rods"],
    "11_Consumiveis": ["consumables", "potions"],
    "12_Materiais": ["materials"],
    "13_Baits": ["baits"],
    "14_Peixes": ["fish"],
    "15_Scrolls": ["scrolls"],
    "16_Premium_e_Presentes": ["premium", "holiday-gifts", "gamepasses"],
    "17_Fighting_Styles": ["fighting-styles"],
}

SKIP_ALT = {
    "image", "icon", "fruit icon", "fruit gif", "transformed", "showcase",
    "blox fruits wiki logo", "blox fruits", "inventory", "items", "accessories",
    "swords", "guns", "skins", "races", "materials", "gears", "fish", "baits",
    "trinkets", "scrolls", "robux", "r$", "quote", "quote 2", "logo",
}
SKIP_PARTS = (
    "damage resistance", "movement speed", "health regeneration", "energy regeneration",
    "melee damage", "sword damage", "fruit damage", "gun damage", "sea damage",
    "all damage", "skill cooldown", "instinct", "air jump", "xp level", "xp mastery",
    "dash distance", "drop chance", "clear vision", "life leech", "fruit meter",
    "stat attribute", "rarity", "recipe", "wiki logo", "navigation", "menu icon",
    "edit icon", "search icon", "discord", "twitter", "youtube", "fandom",
)
GENERIC = re.compile(r"^(image|icon|showcase|gallery|in[- ]?game|transformed|fruit gif|fruit icon)$", re.I)
SCREENSHOT_HINT = re.compile(r"(?:showcase|ingame|in-game|\b0\d*$|\b1\d*$|\b2\d*$|transformed|animation|gif)$", re.I)

FRUIT_NAMES = {
    "Rocket", "Spin", "Blade", "Spring", "Bomb", "Smoke", "Spike", "Flame", "Falcon",
    "Ice", "Sand", "Dark", "Eagle", "Diamond", "Light", "Rubber", "Ghost", "Magma",
    "Quake", "Buddha", "Love", "Creation", "Spider", "Sound", "Phoenix", "Portal", "Rumble",
    "Pain", "Blizzard", "Gravity", "Mammoth", "T-Rex", "Dough", "Shadow", "Venom", "Gas",
    "Spirit", "Tiger", "Yeti", "Kitsune", "Control", "Dragon", "Meme", "Meme (Admin)"
}
MUTATION_NAMES = {"Empyrean", "Fiend", "Werewolf"}
RACE_NAMES = {"Human", "Rabbit", "Shark", "Angel", "Ghoul", "Cyborg", "Draco"}

manifest = []
seen_by_folder = defaultdict(set)
page_cache = {}
failures = []


def fetch(url, binary=False):
    last = None
    for attempt in range(4):
        try:
            r = SESSION.get(url, timeout=30)
            if r.status_code == 200:
                return r.content if binary else r.text
            last = f"HTTP {r.status_code}"
        except Exception as exc:
            last = repr(exc)
        time.sleep(1.2 * (attempt + 1))
    raise RuntimeError(f"Falha ao baixar {url}: {last}")


def soup_for(slug):
    if slug in page_cache:
        return page_cache[slug]
    url = urljoin(BASE, slug.strip("/") + "/")
    try:
        html = fetch(url)
        soup = BeautifulSoup(html, "html.parser")
        page_cache[slug] = (url, soup)
        return url, soup
    except Exception as exc:
        failures.append({"type": "page", "page": slug, "error": str(exc)})
        page_cache[slug] = (url, None)
        return url, None


def clean_text(s):
    s = re.sub(r"\s+", " ", (s or "")).strip()
    return s


def safe_name(name):
    name = clean_text(name)
    name = name.replace("/", " - ").replace("\\", " - ").replace(":", " -")
    name = re.sub(r"[<>\"|?*]", "", name)
    name = re.sub(r"\s+", " ", name).strip(" ._")
    return name[:110] or "sem_nome"


def norm(s):
    s = unicodedata.normalize("NFKD", clean_text(s)).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def img_url(img, page_url):
    raw = img.get("src") or img.get("data-src") or img.get("data-original")
    if not raw:
        srcset = img.get("srcset") or img.get("data-srcset")
        if srcset:
            raw = srcset.split(",")[-1].strip().split(" ")[0]
    if not raw or raw.startswith("data:"):
        return None
    return urljoin(page_url, raw)


def alt_name(img):
    alt = clean_text(img.get("alt") or "")
    if alt.lower().startswith("image:"):
        alt = clean_text(alt.split(":", 1)[1])
    if not alt:
        # hashed source names are useless, but title/parent link often has a name
        alt = clean_text(img.get("title") or "")
    if not alt:
        a = img.find_parent("a")
        if a:
            alt = clean_text(a.get("title") or a.get_text(" ", strip=True))
    return alt


def meaningful_alt(alt):
    n = norm(alt)
    if not n or n in {norm(x) for x in SKIP_ALT} or GENERIC.match(alt):
        return False
    if any(p in n for p in SKIP_PARTS):
        return False
    if len(n) <= 1:
        return False
    return True


def identify_image(data):
    try:
        im = Image.open(io.BytesIO(data))
        frames = getattr(im, "n_frames", 1)
        im.seek(0)
        im = im.convert("RGBA")
        return im, frames
    except Exception:
        return None, 0


def icon_score(im, alt=""):
    w, h = im.size
    if w < 24 or h < 24:
        return -100
    ratio = w / max(h, 1)
    score = 0
    if 0.72 <= ratio <= 1.38:
        score += 5
    elif 0.55 <= ratio <= 1.8:
        score += 2
    else:
        score -= 5
    if max(w, h) <= 1024:
        score += 1
    if SCREENSHOT_HINT.search(alt):
        score -= 5
    # Transparencies are usually item icons rather than gameplay screenshots.
    extrema = im.getchannel("A").getextrema()
    if extrema and extrema[0] < 255:
        score += 4
    return score


def save_icon(folder, name, source_url, source_page, kind="auto", force=False):
    name = clean_text(name)
    if not meaningful_alt(name) and not force:
        return False
    key = source_url
    if key in seen_by_folder[folder]:
        return False
    try:
        data = fetch(source_url, binary=True)
        im, frames = identify_image(data)
        if im is None:
            return False
        score = icon_score(im, name)
        if not force and score < 2:
            return False
        w, h = im.size
        # Emoji-ready transparent square, keeping the whole original icon (no crop).
        canvas = Image.new("RGBA", (SIZE, SIZE), (0, 0, 0, 0))
        fitted = ImageOps.contain(im, (SIZE - 12, SIZE - 12), method=Image.Resampling.LANCZOS)
        x = (SIZE - fitted.width) // 2
        y = (SIZE - fitted.height) // 2
        canvas.alpha_composite(fitted, (x, y))
        target_dir = OUT / folder
        target_dir.mkdir(parents=True, exist_ok=True)
        stem = safe_name(name)
        target = target_dir / f"{stem}.png"
        i = 2
        while target.exists():
            # Same visual may appear on multiple pages; do not create exact duplicate bytes.
            try:
                if hashlib.sha1(target.read_bytes()).hexdigest() == hashlib.sha1(data).hexdigest():
                    return False
            except Exception:
                pass
            target = target_dir / f"{stem} ({i}).png"
            i += 1
        canvas.save(target, "PNG", optimize=True)
        seen_by_folder[folder].add(key)
        manifest.append({
            "folder": folder,
            "file": str(target.relative_to(OUT)).replace("\\", "/"),
            "name": name,
            "source_page": source_page,
            "source_image": source_url,
            "original_width": w,
            "original_height": h,
            "frames_original": frames,
            "kind": kind,
            "score": score,
        })
        return True
    except Exception as exc:
        failures.append({"type": "image", "name": name, "url": source_url, "error": str(exc)})
        return False


def header_map(table):
    rows = table.find_all("tr")
    if not rows:
        return [], []
    headers = [norm(x.get_text(" ", strip=True)) for x in rows[0].find_all(["th", "td"])]
    return headers, rows[1:]


def extract_tables(slug, folder, wanted_headers=None, kind="table"):
    page_url, soup = soup_for(slug)
    if soup is None:
        return 0
    count = 0
    for table in soup.find_all("table"):
        headers, rows = header_map(table)
        if not headers:
            continue
        for row in rows:
            cells = row.find_all(["td", "th"], recursive=False)
            if not cells:
                cells = row.find_all("td")
            if not cells:
                continue
            # Get a human row name from a Name column first.
            row_name = ""
            for idx, h in enumerate(headers):
                if idx < len(cells) and h in ("name", "item", "fruit", "accessory", "sword", "gun", "race", "skin"):
                    row_name = clean_text(cells[idx].get_text(" ", strip=True))
                    if row_name:
                        break
            for idx, cell in enumerate(cells):
                h = headers[idx] if idx < len(headers) else ""
                if wanted_headers and not any(x in h for x in wanted_headers):
                    continue
                for img in cell.find_all("img"):
                    u = img_url(img, page_url)
                    if not u:
                        continue
                    alt = alt_name(img)
                    # The actual item name is often in Name while the icon's alt can be generic.
                    nm = alt if meaningful_alt(alt) else row_name
                    if not nm:
                        continue
                    if save_icon(folder, nm, u, page_url, f"{kind}:{h}", force=bool(row_name)):
                        count += 1
    return count


def extract_all_square(slug, folder, kind="fallback", name_allow=None, name_deny=None):
    page_url, soup = soup_for(slug)
    if soup is None:
        return 0
    count = 0
    main = soup.find("main") or soup.find("article") or soup
    for img in main.find_all("img"):
        u = img_url(img, page_url)
        alt = alt_name(img)
        if not u or not meaningful_alt(alt):
            continue
        n = norm(alt)
        if name_allow and not name_allow(alt, n):
            continue
        if name_deny and name_deny(alt, n):
            continue
        if save_icon(folder, alt, u, page_url, kind):
            count += 1
    return count


def fruit_allow(alt, n):
    # Physical fruit files normally include 'Fruit' in the alt text.
    return "fruit" in n and not any(x in n for x in ("damage", "meter", "dealer", "notifier"))


def permanent_allow(alt, n):
    # UI/hotbar icons are usually named by the fruit without the 'Fruit' suffix.
    base = re.sub(r"\s+fruit$", "", alt, flags=re.I).strip()
    return base in FRUIT_NAMES and "fruit" not in n


def mutation_allow(alt, n):
    return any(norm(x) in n for x in MUTATION_NAMES)


def race_allow(alt, n):
    return any(n == norm(x) or n.startswith(norm(x) + " ") for x in RACE_NAMES)


def collect_fruits():
    # Physical fruits: prefer columns/alt explicitly mentioning Fruit.
    extract_tables("blox-fruits", "02_Frutas_Fisicas", wanted_headers=["fruit", "physical", "image"], kind="fruit-physical")
    extract_all_square("blox-fruits", "02_Frutas_Fisicas", "fruit-physical-fallback", name_allow=fruit_allow)

    # Permanent logos: list page/table icons plus individual article Icon images.
    extract_tables("permanent-blox-fruits", "01_Frutas_Permanentes", wanted_headers=["icon", "fruit", "name"], kind="permanent")
    extract_all_square("permanent-blox-fruits", "01_Frutas_Permanentes", "permanent-fallback", name_allow=lambda a,n: permanent_allow(a,n) or "permanent" in n)

    # Fetch every fruit article to fill any gaps. The first table/infobox usually includes
    # both physical Fruit Icon and hotbar Icon; use the alt labels to place each correctly.
    for fruit in sorted(FRUIT_NAMES):
        slug = fruit.lower().replace(" ", "-").replace("(", "").replace(")", "")
        if fruit == "T-Rex": slug = "t-rex"
        page_url, soup = soup_for(slug)
        if soup is None:
            continue
        for img in soup.find_all("img"):
            u = img_url(img, page_url)
            if not u:
                continue
            alt = alt_name(img)
            n = norm(alt)
            # On individual pages, generic 'Fruit Icon' and 'Icon' labels may be used.
            if n == "fruit icon":
                save_icon("02_Frutas_Fisicas", f"{fruit} Fruit", u, page_url, "fruit-article", force=True)
            elif n == "icon":
                save_icon("01_Frutas_Permanentes", fruit, u, page_url, "fruit-article-icon", force=True)


def collect_skins():
    skin_pages = ["skins", "aura/skins", "dragon/skins", "empyrean/skins", "pain/skins", "lightning/skins", "portal/skins", "diamond/skins", "eagle/skins", "bomb/skins"]
    for slug in skin_pages:
        # Icon column = emoji/logo; Fruit/Mug/Fruit column = physical skin.
        extract_tables(slug, "03_Skins/01_Icones", wanted_headers=["icon"], kind="skin-icon")
        if slug != "aura/skins":
            extract_tables(slug, "03_Skins/02_Fisicas", wanted_headers=["fruit", "mug", "physical"], kind="skin-physical")
    # Aura has no physical fruit counterpart, but its icons are part of Skins.
    extract_all_square("aura/skins", "03_Skins/01_Icones", "aura-fallback", name_allow=lambda a,n: "aura" in n or "icon" in n)


def collect_mutations():
    for slug, display in [("empyrean", "Empyrean"), ("fiend", "Fiend"), ("werewolf", "Werewolf")]:
        page_url, soup = soup_for(slug)
        if soup is None:
            continue
        for img in soup.find_all("img"):
            u = img_url(img, page_url)
            if not u:
                continue
            n = norm(alt_name(img))
            if n == "fruit icon":
                save_icon("04_Mutacoes/02_Fisicas", f"{display} Fruit", u, page_url, "mutation-physical", force=True)
            elif n == "icon":
                save_icon("04_Mutacoes/01_Icones", display, u, page_url, "mutation-icon", force=True)
        extract_all_square(slug, "04_Mutacoes/01_Icones", "mutation-fallback", name_allow=mutation_allow)


def collect_races():
    extract_tables("races", "05_Racas", wanted_headers=["icon", "race", "image", "name"], kind="race")
    extract_all_square("races", "05_Racas", "race-list", name_allow=race_allow)
    for race in sorted(RACE_NAMES):
        slug = race.lower()
        page_url, soup = soup_for(slug)
        if soup is None:
            continue
        # Grab square, meaningful race visuals; keep V1/V2/V3/V4 icons if present too.
        extract_all_square(slug, "05_Racas", "race-article", name_allow=lambda a,n,r=norm(race): n == r or n.startswith(r + " ") or (r in n and ("v1" in n or "v2" in n or "v3" in n or "v4" in n)))


def collect_standard():
    specs = [
        ("06_Espadas", "swords", ["icon", "sword", "name", "image"]),
        ("07_Armas", "guns", ["icon", "gun", "name", "image"]),
        ("08_Acessorios", "accessories", ["icon", "accessory", "name", "image"]),
        ("09_Trinkets", "trinkets", ["icon", "trinket", "name", "image"]),
        ("10_Gear", "gears", ["icon", "gear", "name", "image"]),
        ("10_Gear", "fishing-rods", ["icon", "rod", "name", "image"]),
        ("11_Consumiveis", "consumables", ["icon", "consumable", "potion", "name", "image"]),
        ("11_Consumiveis", "potions", ["icon", "potion", "name", "image"]),
        ("12_Materiais", "materials", ["icon", "material", "name", "image"]),
        ("13_Baits", "baits", ["icon", "bait", "name", "image"]),
        ("14_Peixes", "fish", ["icon", "fish", "name", "image"]),
        ("15_Scrolls", "scrolls", ["icon", "scroll", "name", "image"]),
        ("16_Premium_e_Presentes", "premium", ["icon", "name", "image", "item"]),
        ("16_Premium_e_Presentes", "holiday-gifts", ["icon", "gift", "name", "image"]),
        ("16_Premium_e_Presentes", "gamepasses", ["icon", "gamepass", "name", "image"]),
        ("17_Fighting_Styles", "fighting-styles", ["icon", "style", "name", "image"]),
    ]
    for folder, slug, headers in specs:
        n = extract_tables(slug, folder, wanted_headers=headers, kind="category-table")
        # Category pages like Accessories use cards instead of a conventional table.
        # Square transparent images with meaningful alts reliably capture those icons.
        n += extract_all_square(slug, folder, "category-fallback")
        print(f"{folder:28} {slug:20} +{n}")


def add_inventory_safety_net():
    # Rarity pages enumerate items across categories. We only use them to discover
    # icons that may be absent from a category page after a wiki layout change.
    map_words = [
        ("sword", "06_Espadas"), ("gun", "07_Armas"), ("accessory", "08_Acessorios"),
        ("trinket", "09_Trinkets"), ("gear", "10_Gear"), ("potion", "11_Consumiveis"),
        ("material", "12_Materiais"), ("bait", "13_Baits"), ("fish", "14_Peixes"),
        ("scroll", "15_Scrolls"),
    ]
    for rarity in ("common", "uncommon", "rare", "legendary", "mythical"):
        page_url, soup = soup_for(rarity)
        if soup is None:
            continue
        for img in soup.find_all("img"):
            alt = alt_name(img)
            n = norm(alt)
            u = img_url(img, page_url)
            if not u or not meaningful_alt(alt):
                continue
            # infer category from nearest heading/text container
            context = clean_text((img.find_parent("tr") or img.parent).get_text(" ", strip=True)).lower()
            for word, folder in map_words:
                if word in context:
                    save_icon(folder, alt, u, page_url, f"rarity-safety:{rarity}")
                    break


def write_metadata():
    OUT.mkdir(parents=True, exist_ok=True)
    counts = defaultdict(int)
    for x in manifest:
        counts[x["folder"]] += 1
    summary = {
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "emoji_size": f"{SIZE}x{SIZE} PNG RGBA",
        "total_png": len(manifest),
        "counts": dict(sorted(counts.items())),
        "failed_downloads": len(failures),
        "source": BASE,
    }
    (OUT / "manifest.json").write_text(json.dumps({"summary": summary, "files": manifest, "failures": failures}, indent=2, ensure_ascii=False), encoding="utf-8")
    with (OUT / "manifest.csv").open("w", newline="", encoding="utf-8-sig") as f:
        fieldnames = ["folder", "file", "name", "source_page", "source_image", "original_width", "original_height", "frames_original", "kind", "score"]
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader(); w.writerows(manifest)
    lines = [
        "BLOX FRUITS — PACK DE EMOJIS COMPLETO",
        "======================================",
        "",
        f"Gerado automaticamente em: {summary['generated_utc']}",
        f"Formato: PNG RGBA {SIZE}x{SIZE}, fundo transparente, sem recorte do objeto.",
        f"Total de PNGs no pack: {len(manifest)}",
        "",
        "PASTAS / CONTAGENS",
    ]
    for k, v in sorted(counts.items()):
        lines.append(f"- {k}: {v}")
    lines += [
        "",
        "ESCOPO",
        "- Logos/ícones de frutas permanentes",
        "- Frutas físicas",
        "- Skins: ícones e versões físicas quando existentes",
        "- Mutations: ícones e versões físicas",
        "- Raças",
        "- Espadas",
        "- Armas (Guns)",
        "- Acessórios",
        "- Trinkets",
        "- Gear / varas de pesca",
        "- Consumíveis / poções",
        "- Materiais",
        "- Baits",
        "- Peixes",
        "- Scrolls",
        "- Premium / presentes / gamepasses quando listados",
        "- Fighting Styles (extra para deixar o pack mais abrangente)",
        "",
        "FONTES E ATRIBUIÇÃO",
        "- Dados e imagens coletados de páginas públicas do arquivo Blox Fruits Wiki:",
        f"  {BASE}",
        "- O arquivo mantém a URL de origem de cada imagem em manifest.json e manifest.csv.",
        "- Este é um fan pack não oficial. Blox Fruits, Roblox e os assets do jogo pertencem aos respectivos titulares.",
        "- O texto/referências do wiki são disponibilizados pelo arquivo sob CC BY-SA 4.0; imagens e marcas podem ter direitos próprios.",
        "",
        "OBSERVAÇÃO",
        "A wiki é atualizada ao longo do tempo. O manifest registra exatamente o que foi coletado nesta geração.",
    ]
    (OUT / "LEIA-ME.txt").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps(summary, indent=2, ensure_ascii=False))


def make_zip():
    FINAL_ZIP.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(FINAL_ZIP, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for p in sorted(OUT.rglob("*")):
            if p.is_file():
                z.write(p, p.relative_to(OUT.parent))
    print(f"ZIP={FINAL_ZIP} bytes={FINAL_ZIP.stat().st_size}")


def main():
    if OUT.exists():
        import shutil; shutil.rmtree(OUT)
    collect_fruits()
    collect_skins()
    collect_mutations()
    collect_races()
    collect_standard()
    add_inventory_safety_net()
    write_metadata()
    make_zip()


if __name__ == "__main__":
    main()
