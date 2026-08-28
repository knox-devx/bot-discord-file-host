#!/usr/bin/env python3
import csv, io, json, re, time, zipfile, unicodedata, hashlib, html
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import unquote

import requests
from bs4 import BeautifulSoup
from PIL import Image, ImageOps, ImageSequence

API = "https://blox-fruits.fandom.com/api.php"
OUT = Path("build/blox_fruits_emoji_pack")
ZIP = Path("build/Blox_Fruits_Emoji_Pack_COMPLETO.zip")
SIZE = 256
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) BloxFruitsEmojiPack/3.0"
S = requests.Session(); S.headers.update({"User-Agent": UA, "Accept": "application/json,text/html,*/*"})

CURRENT_FRUITS = [
"Rocket","Spin","Blade","Spring","Bomb","Smoke","Spike","Flame","Ice","Sand","Dark","Eagle","Diamond","Light","Rubber","Ghost","Magma","Quake","Buddha","Love","Creation","Spider","Sound","Phoenix","Portal","Lightning","Pain","Blizzard","Gravity","Mammoth","T-Rex","Dough","Shadow","Venom","Gas","Spirit","Tiger","Yeti","Kitsune","Control","Dragon"
]
RACES = ["Human","Rabbit","Shark","Angel","Ghoul","Cyborg","Draco"]
MUTATIONS = ["Empyrean","Fiend","Werewolf"]
SKIN_PAGES = ["Skins","Aura/Skins","Dragon/Skins","Empyrean/Skins","Pain/Skins","Lightning/Skins","Portal/Skins","Diamond/Skins","Eagle/Skins","Bomb/Skins"]

CATEGORY_SPECS = {
"06_Espadas": ["Swords"],
"07_Armas": ["Guns"],
"08_Acessorios": ["Accessories"],
"09_Trinkets": ["Trinkets"],
"10_Gear": ["Gears", "Gear", "Fishing Rods"],
"11_Consumiveis": ["Consumables", "Potions", "Foods"],
"12_Materiais": ["Materials"],
"13_Baits": ["Baits"],
"14_Peixes": ["Fish"],
"15_Scrolls": ["Scrolls"],
"16_Premium_e_Presentes": ["Gamepasses", "Holiday Gifts", "Premium"],
"17_Fighting_Styles": ["Fighting Styles"],
"18_Outros_Itens": ["Items"]
}

BAD_STEM = (
"showcase","in game","ingame","location","dialogue","npc","move","ability","transformed","transformation","vfx","damage","mastery","quote","logo","wiki","navigation","menu","edit","search","twitter","discord","youtube","banner","background","thumbnail","map","trailer","screenshot","comparison","stats","stat icon","beli","fragments","robux","currency","rarity","recipe","crafting","drop chance","movement speed","health regeneration","energy regeneration","resistance","cooldown","air jump","dash distance","clear vision","fruit meter"
)
BAD_FILES = {"Blox Fruits.png","Blox Fruits Wiki.png","Inventory.png","Items.png","Swords.png","Guns.png","Accessories.png","Materials.png","Skins.png","Races.png","Fish.png","Scrolls.png","Trinkets.png"}

manifest=[]; failures=[]; unresolved=[]
seen_output=defaultdict(set)
imageinfo_cache={}
page_images_cache={}


def nrm(s):
    s=unicodedata.normalize("NFKD", str(s or "")).encode("ascii","ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+"," ",s).strip()

def clean(s): return re.sub(r"\s+"," ",str(s or "")).strip()
def stem(file_title):
    x=re.sub(r"^(?:File|Image):","",file_title,flags=re.I)
    x=re.sub(r"\.[A-Za-z0-9]{2,5}$","",x)
    return clean(x.replace("_"," "))
def safe(s):
    s=clean(s).replace("/"," - ").replace("\\"," - ").replace(":"," -")
    s=re.sub(r'[<>"|?*]','',s).strip(' ._')
    return (s[:115] or "sem_nome")
def bad_file(title):
    st=nrm(stem(title))
    return title in BAD_FILES or any(x in st for x in BAD_STEM)

def api(params, timeout=25):
    p={"format":"json","formatversion":"2"}; p.update(params)
    r=S.get(API,params=p,timeout=timeout)
    r.raise_for_status(); return r.json()

def all_page_images(title):
    if title in page_images_cache:return page_images_cache[title]
    out=[]; cont={}
    for _ in range(30):
        try:
            q={"action":"query","prop":"images","titles":title,"imlimit":"max"}; q.update(cont)
            d=api(q); pages=d.get("query",{}).get("pages",[])
            if not pages or pages[0].get("missing"):break
            out += [x["title"] for x in pages[0].get("images",[])]
            if "continue" not in d:break
            cont=d["continue"]
        except Exception as e:
            failures.append({"type":"page-images","page":title,"error":repr(e)}); break
    page_images_cache[title]=list(dict.fromkeys(out)); return page_images_cache[title]

def category_members(category):
    out=[]; cont={}
    for _ in range(40):
        try:
            q={"action":"query","list":"categorymembers","cmtitle":"Category:"+category,"cmtype":"page","cmlimit":"max"}; q.update(cont)
            d=api(q); out += [x["title"] for x in d.get("query",{}).get("categorymembers",[])]
            if "continue" not in d:break
            cont=d["continue"]
        except Exception as e:
            failures.append({"type":"category","category":category,"error":repr(e)}); break
    return list(dict.fromkeys(out))

def parse_html(page):
    try:
        d=api({"action":"parse","page":page,"prop":"text"})
        return BeautifulSoup(d.get("parse",{}).get("text",""),"html.parser")
    except Exception as e:
        failures.append({"type":"parse","page":page,"error":repr(e)}); return None

def file_from_img(img):
    for key in ("data-image-name","data-image-key"):
        v=img.get(key)
        if v:return "File:"+unquote(v.replace("%20"," "))
    a=img.find_parent("a")
    if a:
        href=a.get("href") or ""
        m=re.search(r"/wiki/(?:File|Image):([^?#]+)",href,re.I)
        if m:return "File:"+unquote(m.group(1)).replace("_"," ")
        title=a.get("title") or ""
        if re.match(r"^(?:File|Image):",title,re.I):return re.sub(r"^Image:","File:",title,flags=re.I)
    alt=clean(img.get("alt") or "")
    if alt and re.search(r"\.(?:png|jpe?g|gif|webp)$",alt,re.I):return "File:"+alt
    return None

def resolve_files(titles):
    todo=[t for t in dict.fromkeys(titles) if t and t not in imageinfo_cache]
    for i in range(0,len(todo),40):
        batch=todo[i:i+40]
        try:
            d=api({"action":"query","titles":"|".join(batch),"prop":"imageinfo","iiprop":"url|mime|size","redirects":"1"})
            for p in d.get("query",{}).get("pages",[]):
                title=p.get("title")
                ii=(p.get("imageinfo") or [None])[0]
                if title and ii:imageinfo_cache[title]=ii
            # Map normalized titles because redirects/case normalization can alter names.
            for requested in batch:
                if requested in imageinfo_cache:continue
                rn=nrm(stem(requested))
                for got,ii in list(imageinfo_cache.items()):
                    if nrm(stem(got))==rn:
                        imageinfo_cache[requested]=ii; break
        except Exception as e:
            failures.append({"type":"imageinfo","files":batch,"error":repr(e)})

def score_general(item, file_title):
    it=nrm(item); st=nrm(stem(file_title))
    if not st or bad_file(file_title):return -999
    # Exact item filenames win strongly.
    if st==it:return 120
    if st==it+" icon" or st=="icon "+it:return 115
    if st.startswith(it+" icon "):return 105
    if st.startswith(it+" "):
        # variants/grades/forms of the same item remain useful.
        return 90
    words=[w for w in it.split() if len(w)>1]
    if words and all(w in st.split() for w in words):return 70
    return -50

def choose_general(item, files, multi=False):
    ranked=sorted([(score_general(item,f),f) for f in files],reverse=True)
    good=[f for s,f in ranked if s>=70]
    if multi:return good[:8]
    return good[:1]

def fruit_physical_files(name, files):
    it=nrm(name); out=[]
    for f in files:
        st=nrm(stem(f))
        if bad_file(f):continue
        if not st.startswith(it+" "):continue
        if "fruit" not in st:continue
        if any(x in st for x in ("icon","meter","notifier","dealer")):continue
        out.append(f)
    # exact <Fruit> Fruit first, then Dragon East/West style variants.
    out.sort(key=lambda f:(0 if nrm(stem(f))==it+" fruit" else 1,len(stem(f))))
    return list(dict.fromkeys(out))[:5]

def fruit_logo_files(name, files):
    it=nrm(name); out=[]
    for f in files:
        st=nrm(stem(f))
        if bad_file(f) or "fruit" in st:continue
        if st in (it,it+" icon","icon "+it):out.append(f)
    return list(dict.fromkeys(out))[:2]

def rendered_table_assets(page, icon_folder, physical_folder=None):
    soup=parse_html(page)
    if not soup:return []
    found=[]
    for table in soup.find_all("table"):
        rows=table.find_all("tr")
        if not rows:continue
        headers=[nrm(x.get_text(" ",strip=True)) for x in rows[0].find_all(["th","td"])]
        for row in rows[1:]:
            cells=row.find_all(["td","th"],recursive=False)
            if not cells:continue
            row_name=""
            for idx,h in enumerate(headers):
                if idx<len(cells) and h in ("name","skin","fruit","item","race","sword","gun","accessory"):
                    row_name=clean(cells[idx].get_text(" ",strip=True));
                    if row_name:break
            for idx,c in enumerate(cells):
                h=headers[idx] if idx<len(headers) else ""
                folder=None
                if "icon" in h: folder=icon_folder
                elif physical_folder and any(x in h for x in ("fruit","mug","physical")):folder=physical_folder
                if not folder:continue
                for im in c.find_all("img"):
                    ft=file_from_img(im)
                    if not ft:continue
                    nm=row_name or stem(ft)
                    found.append((folder,nm,ft,page,"table:"+h))
    return found

def rendered_page_images(page):
    soup=parse_html(page)
    if not soup:return []
    out=[]
    for im in soup.find_all("img"):
        ft=file_from_img(im)
        if not ft or bad_file(ft):continue
        alt=clean(im.get("alt") or "")
        out.append((alt or stem(ft),ft))
    return list(dict.fromkeys(out))

candidates=[]; candidate_seen=set()
def add_candidate(folder,name,file_title,source_page,kind):
    if not file_title:return
    key=(folder,file_title)
    if key in candidate_seen:return
    candidate_seen.add(key)
    candidates.append({"folder":folder,"name":clean(name) or stem(file_title),"file_title":file_title,"source_page":source_page,"kind":kind})


def collect_fruits():
    main_files=all_page_images("Blox Fruits")
    # Main page is useful for exact UI/hotbar logos.
    for fruit in CURRENT_FRUITS:
        files=list(dict.fromkeys(all_page_images(fruit)+main_files))
        logos=fruit_logo_files(fruit,files)
        phys=fruit_physical_files(fruit,files)
        for f in logos:add_candidate("01_Frutas_Permanentes",fruit,f,"Blox Fruits / "+fruit,"fruit-logo")
        for f in phys:add_candidate("02_Frutas_Fisicas",fruit+" Fruit",f,fruit,"fruit-physical")
        if not logos:unresolved.append({"type":"fruit-logo","name":fruit})
        if not phys:unresolved.append({"type":"fruit-physical","name":fruit})


def collect_skins():
    for page in SKIN_PAGES:
        rows=rendered_table_assets(page,"03_Skins/01_Icones",None if page=="Aura/Skins" else "03_Skins/02_Fisicas")
        for folder,nm,ft,src,kind in rows:add_candidate(folder,nm,ft,src,kind)
        # Fallback: skin subpages often expose obvious * Fruit files and icon files even if layout is cards.
        if not rows:
            for nm,ft in rendered_page_images(page):
                st=nrm(stem(ft))
                if "fruit" in st and page!="Aura/Skins": add_candidate("03_Skins/02_Fisicas",nm,ft,page,"skin-fallback-physical")
                elif "skin" in st or "aura" in st or "icon" in st: add_candidate("03_Skins/01_Icones",nm,ft,page,"skin-fallback-icon")


def collect_mutations():
    for m in MUTATIONS:
        files=all_page_images(m)
        phys=fruit_physical_files(m,files)
        logos=fruit_logo_files(m,files)
        # Mutation images may be prefixed by the source fruit rather than mutation name; rendered infobox/table is fallback.
        for f in phys:add_candidate("04_Mutacoes/02_Fisicas",m+" Fruit",f,m,"mutation-physical")
        for f in logos:add_candidate("04_Mutacoes/01_Icones",m,f,m,"mutation-icon")
        if not phys or not logos:
            for nm,ft in rendered_page_images(m):
                st=nrm(stem(ft))
                if "fruit" in st and not bad_file(ft):add_candidate("04_Mutacoes/02_Fisicas",m+" Fruit",ft,m,"mutation-rendered-physical")
                elif (nrm(m) in st or st==nrm(m)+" icon") and not bad_file(ft):add_candidate("04_Mutacoes/01_Icones",m,ft,m,"mutation-rendered-icon")


def collect_races():
    for race in RACES:
        files=all_page_images(race)
        selected=choose_general(race,files,multi=True)
        if not selected:
            imgs=rendered_page_images(race)
            selected=[ft for nm,ft in imgs if nrm(race) in nrm(stem(ft))][:6]
        if not selected:unresolved.append({"type":"race","name":race})
        for f in selected:add_candidate("05_Racas",stem(f),f,race,"race")


def category_union(names):
    members=[]
    for c in names:
        got=category_members(c)
        if got:members += got
    return list(dict.fromkeys(members))

def collect_categories():
    for folder,cats in CATEGORY_SPECS.items():
        members=category_union(cats)
        # Category page itself sometimes uses a different category structure; parse/list images as fallback.
        if folder=="18_Outros_Itens":
            members=[m for m in members if m not in CURRENT_FRUITS]
        print("CATEGORY",folder,"members",len(members),flush=True)
        def work(item): return item,all_page_images(item)
        with ThreadPoolExecutor(max_workers=16) as ex:
            fs=[ex.submit(work,item) for item in members]
            for fut in as_completed(fs):
                item,files=fut.result()
                chosen=choose_general(item,files,multi=(folder in ("09_Trinkets","10_Gear","11_Consumiveis","14_Peixes")))
                for f in chosen:add_candidate(folder,stem(f) if len(chosen)>1 else item,f,item,"category-member")
                if not chosen:unresolved.append({"type":"category-item","folder":folder,"name":item})
        # Add table-rendered exact icons that category membership can miss (grades, variants, gifts).
        for page in cats:
            for nm,ft in rendered_page_images(page):
                st=nrm(stem(ft))
                if bad_file(ft):continue
                # avoid obvious fruit navigation pollution in non-fruit categories
                fruit_stems={nrm(x) for x in CURRENT_FRUITS}|{nrm(x+" Fruit") for x in CURRENT_FRUITS}
                if st in fruit_stems and folder not in ("16_Premium_e_Presentes",):continue
                add_candidate(folder,nm,ft,page,"category-page-fallback")


def imageinfo_for(title):
    if title not in imageinfo_cache:resolve_files([title])
    return imageinfo_cache.get(title)

def dl_convert(c):
    ii=imageinfo_for(c["file_title"])
    if not ii:return {"error":"imageinfo missing","c":c}
    url=ii.get("url")
    if not url:return {"error":"url missing","c":c}
    try:
        r=requests.get(url,headers={"User-Agent":UA},timeout=30);r.raise_for_status()
        im=Image.open(io.BytesIO(r.content)); frames=getattr(im,"n_frames",1); im.seek(0); rgba=im.convert("RGBA")
        w,h=rgba.size
        if w<16 or h<16:return {"error":"too small","c":c}
        # Reject clear screenshots unless this is a table-labelled asset or exact item match.
        ratio=w/max(h,1)
        exact=score_general(c["name"],c["file_title"])>=100
        trusted=c["kind"].startswith("table") or "fruit" in c["kind"] or "mutation" in c["kind"]
        if not trusted and not exact and not (0.45<=ratio<=2.2):return {"error":"screenshot ratio","c":c}
        canvas=Image.new("RGBA",(SIZE,SIZE),(0,0,0,0))
        fit=ImageOps.contain(rgba,(SIZE-12,SIZE-12),Image.Resampling.LANCZOS)
        canvas.alpha_composite(fit,((SIZE-fit.width)//2,(SIZE-fit.height)//2))
        b=io.BytesIO();canvas.save(b,"PNG",optimize=True)
        return {"c":c,"data":b.getvalue(),"source_url":url,"w":w,"h":h,"frames":frames,"mime":ii.get("mime")}
    except Exception as e:return {"error":repr(e),"c":c}

def build_files():
    resolve_files([c["file_title"] for c in candidates])
    results=[]
    with ThreadPoolExecutor(max_workers=20) as ex:
        fs=[ex.submit(dl_convert,c) for c in candidates]
        for i,f in enumerate(as_completed(fs),1):
            r=f.result()
            if "error" in r:
                failures.append({"type":"download","name":r["c"]["name"],"file":r["c"]["file_title"],"error":r["error"]})
            else:results.append(r)
            if i%100==0:print("FILES",i,"/",len(fs),flush=True)
    hashes=defaultdict(set); used=defaultdict(set)
    for r in sorted(results,key=lambda x:(x["c"]["folder"],x["c"]["name"],x["c"]["file_title"])):
        c=r["c"]; hh=hashlib.sha1(r["data"]).hexdigest()
        if hh in hashes[c["folder"]]:continue
        hashes[c["folder"]].add(hh)
        d=OUT/c["folder"];d.mkdir(parents=True,exist_ok=True)
        base=safe(c["name"]); fn=base+".png"; k=2
        while fn.lower() in used[c["folder"]]:fn=f"{base} ({k}).png";k+=1
        used[c["folder"]].add(fn.lower()); p=d/fn;p.write_bytes(r["data"])
        manifest.append({"folder":c["folder"],"file":str(p.relative_to(OUT)).replace("\\","/"),"name":c["name"],"fandom_file":c["file_title"],"source_page":c["source_page"],"source_image":r["source_url"],"original_width":r["w"],"original_height":r["h"],"frames_original":r["frames"],"mime":r["mime"],"kind":c["kind"]})

def metadata_zip():
    OUT.mkdir(parents=True,exist_ok=True)
    counts=defaultdict(int)
    for x in manifest:counts[x["folder"]]+=1
    summary={"generated_utc":time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime()),"source":"Blox Fruits Fandom MediaWiki API + static Wikia CDN","current_fruits_expected":41,"total_png":len(manifest),"format":f"PNG RGBA {SIZE}x{SIZE}","counts":dict(sorted(counts.items())),"unresolved_count":len(unresolved),"failure_count":len(failures)}
    (OUT/"manifest.json").write_text(json.dumps({"summary":summary,"files":manifest,"unresolved":unresolved,"failures":failures},ensure_ascii=False,indent=2),encoding="utf-8")
    fields=["folder","file","name","fandom_file","source_page","source_image","original_width","original_height","frames_original","mime","kind"]
    with (OUT/"manifest.csv").open("w",newline="",encoding="utf-8-sig") as f:
        w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(manifest)
    readme=["BLOX FRUITS — PACK DE EMOJIS", "", "Pack não oficial montado com assets públicos reais do Blox Fruits Wiki/Fandom, sem recriar os itens por IA.", f"Gerado: {summary['generated_utc']}", f"Total de PNGs: {len(manifest)}", f"Formato: PNG RGBA {SIZE}x{SIZE}, fundo transparente/padding sem cortar o objeto.", "", "Pastas:"]
    readme += [f"- {k}: {v}" for k,v in sorted(counts.items())]
    readme += ["", "O manifest.json e manifest.csv guardam o arquivo Fandom, página de origem e URL de cada asset.", "Fonte: https://blox-fruits.fandom.com/ (MediaWiki API) e static.wikia.nocookie.net.", "Blox Fruits, Roblox, nomes, marcas e assets pertencem aos respectivos titulares. Este ZIP é apenas um fan pack organizado para uso pessoal/emoji."]
    (OUT/"LEIA-ME.txt").write_text("\n".join(readme),encoding="utf-8")
    ZIP.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(ZIP,"w",zipfile.ZIP_DEFLATED,compresslevel=9) as z:
        for p in sorted(OUT.rglob("*")):
            if p.is_file():z.write(p,p.relative_to(OUT.parent))
    print("SUMMARY="+json.dumps(summary,ensure_ascii=False),flush=True)
    print("UNRESOLVED_SAMPLE="+json.dumps(unresolved[:40],ensure_ascii=False),flush=True)
    print("ZIP_BYTES="+str(ZIP.stat().st_size),flush=True)

def main():
    import shutil
    if OUT.exists():shutil.rmtree(OUT)
    collect_fruits(); print("CAND fruits",len(candidates),flush=True)
    collect_skins(); print("CAND skins",len(candidates),flush=True)
    collect_mutations(); collect_races(); print("CAND mutations+races",len(candidates),flush=True)
    collect_categories(); print("CAND total",len(candidates),flush=True)
    build_files(); metadata_zip()

if __name__=="__main__": main()
