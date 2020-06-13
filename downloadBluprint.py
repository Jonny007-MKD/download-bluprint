cookies = {"alertSeen":"1","craftsy_countrycode":"DE","craftsy_curcode":"EUR","craftsy_tok":"blabla.longblabla.otherbla","craftsy_userId":"12345","craftsy_visid":"bla-bl-bal-lab-lba"}


import sys
import requests
import urllib.parse
import json
import pickle
import os
import ssl
import re
from datetime import datetime
from urllib3 import poolmanager
try: 
    from BeautifulSoup import BeautifulSoup
except ImportError:
    from bs4 import BeautifulSoup


# Helper to allow older TLS
class TLSAdapter(requests.adapters.HTTPAdapter):
    def init_poolmanager(self, connections, maxsize, block=False):
        """Create and initialize the urllib3 PoolManager."""
        ctx = ssl.create_default_context()
        ctx.set_ciphers('DEFAULT@SECLEVEL=1')
        self.poolmanager = poolmanager.PoolManager(
                num_pools=connections,
                maxsize=maxsize,
                block=block,
                ssl_version=ssl.PROTOCOL_TLS,
                ssl_context=ctx)

# A Class in mybluprint
class Class:
    def __init__(self, a):
        self.title = a.text
        self.url = "https://shop.mybluprint.com" + a.attrs['href']
        self.photo = "https:" + a.parent.parent.parent.find('img').attrs['src']
        self.author = a.parent.next_sibling.text
        if self.author.startswith("with "): self.author = self.author[len("with "):]
        self.error = None

class Episode:
    def __init__(self, div):
        self.id = div.attrs["data-ajax-url"].split("/")[-1]
        self.title = div.attrs["data-title"]
        if self.title.startswith("Episode: "): self.title = self.title[len("Episode: "):]
        self.chapters = json.loads(urllib.parse.unquote(div.attrs["data-chapters"]))
        self.url = None
        self.vtt = None
        self.error = None


def scrapeData(session):
    # Fetch information about all classes from the library
    classes = []
    offset = 0
    while True:
        url = f"https://shop.mybluprint.com/account/class-library?offset={offset}&sortBy=MOST_RECENT"
        print(f"Fetching library page {offset}: {url}")
        request = session.get(url)
        assert request.status_code == 200, f"Downloading the library page {offset} failed"
        html = BeautifulSoup(request.text, features="html.parser")
        aa = html.body.findAll('a', attrs={'class', 'title'})
        print(f"  Found {len(aa)} classes")
        if not aa: break
        classes.extend([ Class(a) for a in aa ])
        offset += 1


    # Fetch information about all episodes of all classes
    for c in classes:
        try:
            print(f"Fetching front page of '{c.title}': {c.url}")
            request = session.get(c.url)
            if request.status_code != 200:
                print(f"!!! Downloading the front page of class \"{c.title}\" failed")
                c.error = f"Downloading front page failed: {request.status_code}"
                continue
            html = BeautifulSoup(request.text, features="html.parser")
            watch_class = html.body.find("span", text="Watch class")
            
            url = watch_class.parent.attrs["href"]
            print(f"  Fetching page of first episode: {url}")
            request = session.get(url)
            if request.status_code != 200:
                print(f"!!! Downloading the first episode page of class \"{c.title}\" failed")
                c.error = f"Downloading first episode page failed: {request.status_code}"
                continue
            html = BeautifulSoup(request.text, features="html.parser")

            divs = html.body.find("div", id="episodes").findAll('div', attrs={"class":"PlaylistItem"})
            c.episodes = [ Episode(div) for div in divs ]
        except Exception as e:
            print(f"!!! Error: {e}")
            c.error = str(e)
            continue
        print(f"  Found {len(c.episodes)} episodes")
    return classes

def loadCache():
    if not os.path.isfile("classes.cache"): return None
    with open("classes.cache", "rb") as cache:
        try:
            loaded = pickle.load(cache)
            if not hasattr(loaded, "date"): return None
            if (datetime.now() - loaded.date).total_seconds() > 86400: return None
            return loaded.data
        except:
            return None

class CachedData:
    def __init__(self, classes):
        self.data = classes
        self.date = datetime.now()
def writeCache(classes):
    with open("classes.cache", "wb") as cache:
        pickle.dump(CachedData(classes), cache)
    return

def makeValidPath(path):
    return re.sub("[<>:\"/\\|?*]", "", path)

def downloadClass(session, c):
    def scrapeEpisodes():
        for e in c.episodes:
            if e.url and e.vtt: continue
            try:
                url = f"https://api.mybluprint.com/m/videos/secure/episodes/{e.id}"
                print(f"  Fetching location of episode {e.title}: {url}")
                request = session.get(url)
                if request.status_code != 200:
                    print(f"!!! Downloading the episode info failed")
                    e.error = f"Downloading episode info failed: {request.status_code}"
                    continue
                data = json.loads(request.text)
                data2 = [ d for d in data if d["url"].endswith("mp4") ]
                assert data2, f"Could not find location of type mp4. Episode id: {e.id}"
                e.url = data2[0]["url"]
                e.vtt = data2[0]["vttUrl"]
            except Exception as e:
                print(f"!!! Error: {e}")
                e.error = str(e)
                continue
        return

    def createDirectory():
        dir = f"{c.title} - {c.author}"
        dir = makeValidPath(dir)
        if not os.path.exists(dir):
            os.makedirs(dir)
        return dir

    def writeClassInfo(dir):
        path = os.path.join(dir, "info.json")
        if os.path.exists(path): return
        data = {
            "title":    c.title,
            "url":      c.url,
            "photo":    c.photo,
            "author":   c.author,
            "episodes": [ e.title for e in c.episodes ],
        }
        if hasattr(c, "error") and c.error: data["error"] = c.error
        with open(path, "w") as f:
            json.dump(data, f, indent=4)

    def downloadFile(url, path):
        if os.path.exists(path): return
        tmp = "downloading.tmp"
        with session.get(url, stream=True) as r:
            if not r:
                print(f"!!!  Error: {r.status_code}")
                return
            total_length = int(r.headers.get('content-length'))
            downloaded_length = 0
            with open(tmp, "wb") as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
                    if total_length > 0:
                        done = int(50 * downloaded_length / total_length)
                        sys.stdout.write("\r[%s%s]" % ('=' * done, ' ' * (50-done)) )   
                        sys.stdout.flush()
                        downloaded_length += len(chunk)
            if total_length:
                sys.stdout.write("\r%s\r" % (' ' * 52))
            os.rename(tmp, path)
        return

    def downloadImage(dir):
        print(f"  Downloading image")
        path = os.path.join(dir, "Folder.jpg")
        downloadFile(c.photo, path)
        return

    def downloadEpisode(i, classDir, e):
        print(f"  Downloading episode {i}: {e.title}")
        def createDirectory():
            dir = os.path.join(classDir, makeValidPath(f"{i} {e.title}"))
            if not os.path.exists(dir):
                os.makedirs(dir)
            return dir

        def writeEpisodeInfo(dir):
            path = os.path.join(dir, "info.json")
            if os.path.exists(path): return
            data = {
                "title":    e.title,
                "id":       e.id,
                "chapters": e.chapters,
                "url":      e.url,
                "vtt":      e.vtt,
            }
            if hasattr(e, "error") and e.error: data["error"] = c.error
            with open(path, "w") as f:
                json.dump(data, f, indent=4)

        def downloadVTT(dir):
            print(f"    Downloading VTT")
            path = os.path.join(dir, makeValidPath(f"{e.title}.vtt"))
            downloadFile(e.vtt, path)

        def downloadVideo(dir):
            print(f"    Downloading Video")
            path = os.path.join(dir, makeValidPath(f"{e.title}.mp4"))
            downloadFile(e.url, path)

        scrapeEpisodes()
        dir = createDirectory()
        writeEpisodeInfo(dir)
        downloadVTT(dir)
        downloadVideo(dir)

    print(f"Downloading class {c.title}")
    dir = createDirectory()
    writeClassInfo(dir)
    downloadImage(dir)
    for i,e in enumerate(c.episodes):
        downloadEpisode(i+1, dir, e)



session = requests.Session()
session.mount('https://', TLSAdapter()) # fixes WRONG_SIGNATURE_TYPE on https://course.mybluprint.com
for k,v in cookies.items(): # Add Cookies to be logged in
    session.cookies[k] = v


classes = loadCache()
if not classes:
    classes = scrapeData(session)
    writeCache(classes)
for c in classes:
    downloadClass(session, c)
    writeCache(classes)