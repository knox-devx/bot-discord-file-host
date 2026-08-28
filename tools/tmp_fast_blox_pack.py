#!/usr/bin/env python3
import csv, io, json, re, time, zipfile, unicodedata, hashlib
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup
from PIL import Image, ImageOps

BASE='https://bloxfruitswiki.org/wiki/'
OUT=Path('build/blox_fruits_emoji_pack')
ZIP=Path('build/Blox_Fruits_Emoji_Pack_COMPLETO.zip')
SIZE=256
HEADERS={'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131 Safari/537.36 BloxEmojiCollector/2.0'}

FRUITS=['Rocket','Spin','Blade','Spring','Bomb','Smoke','Spike','Flame','Ice','Sand','Dark','Eagle','Diamond','Light','Rubber','Ghost','Magma','Quake','Buddha','Love','Creation','Spider','Sound','Phoenix','Portal','Lightning','Pain','Blizzard','Gravity','Mammoth','T-Rex','Dough','Shadow','Venom','Gas','Spirit','Tiger','Yeti','Kitsune','Control','Dragon']
MUTATIONS=['Empyrean','Fiend','Werewolf']
RACES=['Human','Rabbit','Shark','Angel','Ghoul','Cyborg','Draco']
SKIN_PAGES=['skins','aura/skins','dragon/skins','empyrean/skins','pain/skins','lightning/skins','portal/skins','diamond/skins','eagle/skins','bomb/skins']
CATEGORY_PAGES={
'06_Espadas':['swords'], '07_Armas':['guns'], '08_Acessorios':['accessories'],
'09_Trinkets':['trinkets'], '10_Gear':['gears','fishing-rods'],
'11_Consumiveis':['consumables','potions','foods'], '12_Materiais':['materials'],
'13_Baits':['baits'], '14_Peixes':['fish'], '15_Scrolls':['scrolls'],
'16_Premium_e_Presentes':['premium','holiday-gifts','gamepasses'],
'17_Fighting_Styles':['fighting-styles']}

SKIP_PARTS=('damage resistance','movement speed','health regeneration','energy regeneration','melee damage','sword damage','fruit damage','gun damage','sea damage','all damage','skill cooldown','instinct','air jump','xp level','xp mastery','dash distance','drop chance','clear vision','life leech','fruit meter','stat attribute','rarity','recipe','wiki logo','navigation','menu icon','edit icon','search icon','discord','twitter','youtube','robux','quote')
GENERIC={'','image','icon','showcase','gallery','in game','ingame','transformed','fruit gif','fruit icon','blox fruits wiki logo','blox fruits','inventory','items','accessories','swords','guns','skins','races','materials','gears','fish','baits','trinkets','scrolls','r','logo'}

manifest=[]; failures=[]; page_cache={}; candidates=[]; seen_candidate=set(); file_lock=None

def norm(s):
    s=unicodedata.normalize('NFKD',str(s or '')).encode('ascii','ignore').decode().lower()
    return re.sub(r'[^a-z0-9]+',' ',s).strip()

def clean(s): return re.sub(r'\s+',' ',str(s or '')).strip()
def safe(s):
    s=clean(s).replace('/',' - ').replace('\\',' - ').replace(':',' -')
    s=re.sub(r'[<>"|?*]','',s).strip(' ._')
    return s[:110] or 'sem_nome'

def slug(name):
    x=name.lower().replace(' ','-').replace('(','').replace(')','')
    return x

def get(url,binary=False):
    try:
        r=requests.get(url,headers=HEADERS,timeout=10)
        if r.status_code!=200: raise RuntimeError(f'HTTP {r.status_code}')
        return r.content if binary else r.text
    except Exception as e:
        raise RuntimeError(f'{url}: {e}')

def fetch_page(s):
    u=urljoin(BASE,s.strip('/')+'/')
    try: return s,u,BeautifulSoup(get(u),'html.parser'),None
    except Exception as e: return s,u,None,str(e)

def prefetch(slugs):
    unique=list(dict.fromkeys(slugs))
    with ThreadPoolExecutor(max_workers=20) as ex:
        fs=[ex.submit(fetch_page,s) for s in unique]
        for f in as_completed(fs):
            s,u,soup,err=f.result(); page_cache[s]=(u,soup)
            if err: failures.append({'type':'page','page':s,'error':err})

def imgurl(img,page):
    v=img.get('src') or img.get('data-src') or img.get('data-original')
    if not v:
        ss=img.get('srcset') or img.get('data-srcset')
        if ss: v=ss.split(',')[-1].strip().split(' ')[0]
    if not v or v.startswith('data:'): return None
    return urljoin(page,v)

def alt(img):
    a=clean(img.get('alt') or img.get('title') or '')
    if a.lower().startswith('image:'): a=clean(a.split(':',1)[1])
    if not a:
        p=img.find_parent('a')
        if p: a=clean(p.get('title') or p.get_text(' ',strip=True))
    return a

def meaningful(a):
    n=norm(a)
    if n in GENERIC or len(n)<2: return False
    if any(x in n for x in SKIP_PARTS): return False
    return True

def add(folder,name,url,page,kind,force=False):
    if not url: return
    key=(folder,url)
    if key in seen_candidate: return
    if not force and not meaningful(name): return
    seen_candidate.add(key); candidates.append({'folder':folder,'name':clean(name) or 'Item','url':url,'page':page,'kind':kind,'force':force})

def table_extract(page_slug, icon_folder, physical_folder=None):
    page,soup=page_cache.get(page_slug,(urljoin(BASE,page_slug+'/'),None))
    if soup is None:return
    for table in soup.find_all('table'):
        rows=table.find_all('tr')
        if len(rows)<2:continue
        hs=[norm(x.get_text(' ',strip=True)) for x in rows[0].find_all(['th','td'])]
        for row in rows[1:]:
            cells=row.find_all('td',recursive=False) or row.find_all('td')
            if not cells:continue
            rowname=''
            for i,h in enumerate(hs):
                if i<len(cells) and h in ('name','item','skin','fruit','race','sword','gun','accessory'):
                    rowname=clean(cells[i].get_text(' ',strip=True));
                    if rowname:break
            for i,c in enumerate(cells):
                h=hs[i] if i<len(hs) else ''
                target=None; kind='table'
                if 'icon' in h: target=icon_folder; kind='icon-column'
                elif physical_folder and ('fruit' in h or 'mug' in h or 'physical' in h): target=physical_folder; kind='physical-column'
                elif icon_folder and any(x in h for x in ('image','item','sword','gun','accessory','trinket','material','bait','fish','scroll','gear','potion')): target=icon_folder
                if not target:continue
                for im in c.find_all('img'):
                    u=imgurl(im,page); a=alt(im); name=a if meaningful(a) else rowname
                    if name: add(target,name,u,page,kind,force=bool(rowname))

def all_images(page_slug, folder, allow=None, kind='page', force_names=None):
    page,soup=page_cache.get(page_slug,(urljoin(BASE,page_slug+'/'),None))
    if soup is None:return
    root=soup.find('main') or soup.find('article') or soup
    for im in root.find_all('img'):
        a=alt(im); n=norm(a); u=imgurl(im,page)
        if allow and not allow(a,n): continue
        force=bool(force_names and a in force_names)
        add(folder,a,u,page,kind,force=force)

def collect():
    # Blox Fruits main page contains the current shop/hotbar logos.
    all_images('blox-fruits','01_Frutas_Permanentes',allow=lambda a,n:a in FRUITS,kind='fruit-logo',force_names=set(FRUITS))
    # Physical fruits + article icons (article pages use generic Fruit Icon / Icon labels).
    for f in FRUITS:
        s=slug(f); page,soup=page_cache.get(s,(urljoin(BASE,s+'/'),None))
        if soup is None:continue
        for im in soup.find_all('img'):
            a=alt(im); n=norm(a); u=imgurl(im,page)
            if n=='fruit icon': add('02_Frutas_Fisicas',f+' Fruit',u,page,'fruit-physical',True)
            elif n=='icon': add('01_Frutas_Permanentes',f,u,page,'fruit-icon',True)
    # Skins: exact Icon vs Fruit/Mug table columns.
    for s in SKIN_PAGES: table_extract(s,'03_Skins/01_Icones',None if s=='aura/skins' else '03_Skins/02_Fisicas')
    # Mutations.
    all_images('blox-fruits','04_Mutacoes/01_Icones',allow=lambda a,n:any(x.lower() in a.lower() for x in MUTATIONS),kind='mutation-list')
    for m in MUTATIONS:
        page,soup=page_cache.get(slug(m),(urljoin(BASE,slug(m)+'/'),None))
        if soup is None:continue
        for im in soup.find_all('img'):
            n=norm(alt(im)); u=imgurl(im,page)
            if n=='fruit icon': add('04_Mutacoes/02_Fisicas',m+' Fruit',u,page,'mutation-physical',True)
            elif n=='icon': add('04_Mutacoes/01_Icones',m,u,page,'mutation-icon',True)
    # Races: list + article images whose alt starts with race name (V1..V4 included if present).
    all_images('races','05_Racas',allow=lambda a,n:any(n==norm(r) or n.startswith(norm(r)+' ') for r in RACES),kind='race-list')
    for r in RACES:
        all_images(slug(r),'05_Racas',allow=lambda a,n,rr=norm(r):n==rr or n.startswith(rr+' '),kind='race-article')
    # Remaining inventory categories: use tables plus meaningful square icons as fallback.
    for folder,pages in CATEGORY_PAGES.items():
        for s in pages:
            table_extract(s,folder)
            all_images(s,folder,kind='category-fallback')


def dl_one(c):
    try:
        data=get(c['url'],True)
        im=Image.open(io.BytesIO(data)); frames=getattr(im,'n_frames',1); im.seek(0); im=im.convert('RGBA')
        w,h=im.size
        if w<24 or h<24:return None
        ratio=w/max(1,h); alpha=im.getchannel('A').getextrema(); transparent=alpha and alpha[0]<255
        score=(5 if .70<=ratio<=1.42 else 2 if .52<=ratio<=1.9 else -5)+(4 if transparent else 0)+(1 if max(w,h)<=1200 else 0)
        if not c['force'] and score<2:return None
        canvas=Image.new('RGBA',(SIZE,SIZE),(0,0,0,0)); fit=ImageOps.contain(im,(SIZE-12,SIZE-12),Image.Resampling.LANCZOS)
        canvas.alpha_composite(fit,((SIZE-fit.width)//2,(SIZE-fit.height)//2))
        bio=io.BytesIO(); canvas.save(bio,'PNG',optimize=True)
        return c,bio.getvalue(),w,h,frames,score
    except Exception as e:
        return {'error':str(e),'candidate':c}

def download_all():
    results=[]
    with ThreadPoolExecutor(max_workers=24) as ex:
        fs=[ex.submit(dl_one,c) for c in candidates]
        for i,f in enumerate(as_completed(fs),1):
            r=f.result()
            if isinstance(r,dict) and 'error' in r: failures.append({'type':'image','url':r['candidate']['url'],'name':r['candidate']['name'],'error':r['error']})
            elif r: results.append(r)
            if i%100==0: print('downloaded candidates',i,'/',len(fs),flush=True)
    # deterministic output and filename collision handling.
    used=defaultdict(set); hashes=defaultdict(set)
    for c,data,w,h,frames,score in sorted(results,key=lambda x:(x[0]['folder'],x[0]['name'],x[0]['url'])):
        hh=hashlib.sha1(data).hexdigest()
        if hh in hashes[c['folder']]: continue
        hashes[c['folder']].add(hh)
        d=OUT/c['folder']; d.mkdir(parents=True,exist_ok=True)
        stem=safe(c['name']); fn=stem+'.png'; k=2
        while fn.lower() in used[c['folder']]: fn=f'{stem} ({k}).png'; k+=1
        used[c['folder']].add(fn.lower()); p=d/fn; p.write_bytes(data)
        manifest.append({'folder':c['folder'],'file':str(p.relative_to(OUT)).replace('\\','/'),'name':c['name'],'source_page':c['page'],'source_image':c['url'],'original_width':w,'original_height':h,'frames_original':frames,'kind':c['kind'],'score':score})

def metadata():
    counts=defaultdict(int)
    for x in manifest:counts[x['folder']]+=1
    summary={'generated_utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'emoji_format':f'PNG RGBA {SIZE}x{SIZE}','total_png':len(manifest),'counts':dict(sorted(counts.items())),'failures':len(failures),'source':BASE}
    (OUT/'manifest.json').write_text(json.dumps({'summary':summary,'files':manifest,'failures':failures},ensure_ascii=False,indent=2),encoding='utf-8')
    with (OUT/'manifest.csv').open('w',newline='',encoding='utf-8-sig') as f:
        fields=['folder','file','name','source_page','source_image','original_width','original_height','frames_original','kind','score']; w=csv.DictWriter(f,fieldnames=fields);w.writeheader();w.writerows(manifest)
    txt=['BLOX FRUITS — PACK DE EMOJIS COMPLETO','',f"Gerado: {summary['generated_utc']}",f"Total de PNGs: {summary['total_png']}",f"Formato: {summary['emoji_format']}",'','Pastas:']+[f'- {k}: {v}' for k,v in sorted(counts.items())]+['','Inclui frutas permanentes, frutas físicas, skins (ícones e físicas), mutations, raças, espadas, armas, acessórios, trinkets, gear, consumíveis, materiais, baits, peixes, scrolls, premium/presentes e Fighting Styles quando há assets listados.','','Fonte de referência/assets: https://bloxfruitswiki.org/wiki/','Cada arquivo tem a URL exata da imagem de origem em manifest.json e manifest.csv.','Fan pack não oficial. Blox Fruits/Roblox e os assets pertencem aos respectivos titulares. O arquivo da wiki informa uso de conteúdo textual sob CC BY-SA 4.0; imagens/marcas podem ter direitos próprios.']
    (OUT/'LEIA-ME.txt').write_text('\n'.join(txt),encoding='utf-8')
    print('SUMMARY',json.dumps(summary,ensure_ascii=False),flush=True)

def make_zip():
    ZIP.parent.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(ZIP,'w',zipfile.ZIP_DEFLATED,compresslevel=9) as z:
        for p in sorted(OUT.rglob('*')):
            if p.is_file():z.write(p,p.relative_to(OUT.parent))
    print('ZIP',ZIP,ZIP.stat().st_size,flush=True)

def main():
    import shutil
    if OUT.exists():shutil.rmtree(OUT)
    slugs=['blox-fruits','races']+FRUITS+MUTATIONS+RACES+SKIN_PAGES+[s for v in CATEGORY_PAGES.values() for s in v]
    slugs=[slug(x) if x in FRUITS+MUTATIONS+RACES else x for x in slugs]
    prefetch(slugs); collect(); print('CANDIDATES',len(candidates),flush=True); download_all(); metadata(); make_zip()
if __name__=='__main__':main()
