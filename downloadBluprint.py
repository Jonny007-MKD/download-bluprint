import sys
import requests
import urllib.parse
import json
import pickle
import os
import ssl
import re
import platform
from cookies import cookies
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
        """ Create a new Class object from the <a> tag on the website """
        self.title = a.text
        self.url = "https://shop.mybluprint.com" + a.attrs['href']
        self.photo = "https:" + a.parent.parent.parent.find('img').attrs['src']
        self.author = a.parent.next_sibling.text
        if self.author.startswith("with "): self.author = self.author[len("with "):]
        self.error = None
        self.episodes = []
        self.resources = []

# An Episode of a Class
class Episode:
    def __init__(self, div):
        """ Create a new Episode object from the <div> tag on the website """
        self.id = div.attrs["data-ajax-url"].split("/")[-1]
        self.title = div.attrs["data-title"]
        if self.title.startswith("Episode: "): self.title = self.title[len("Episode: "):]
        self.chapters = json.loads(urllib.parse.unquote(div.attrs["data-chapters"]))
        self.url = None
        self.vtt = None
        self.error = None

# A Resource/Material of a Class
class Resource:
    def __init__(self, a):
        self.id = a.attrs["data-material-id"]
        self.url = a.attrs["href"]
        self.title = a.text.strip()

def scrapeData(session):
    """
    Fetch information about all classes from the library on mybluprint.com
    Return a list of Class objects
    """
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
            divs = html.body.find("div", id="materials").findAll('a', attrs={"class":"FileLink"})
            c.resources =  [ Resource(div) for div in divs ]
        except Exception as e:
            print(f"!!! Error: {e}")
            c.error = str(e)
            continue
        print(f"  Found {len(c.episodes)} episodes and {len(c.resources)} resources")
    return classes

def loadCache():
    """ Load the list of Classes from a cache file to allow continuing the downloading """
    if not os.path.isfile("classes.cache"): return None
    with open("classes.cache", "rb") as cache:
        try:
            loaded = pickle.load(cache)
            if not hasattr(loaded, "date"): return None
            if (datetime.now() - loaded.date).total_seconds() > 86400: return None
            return loaded.data
        except:
            return None

# List of Classes with timestamp
class CachedData:
    def __init__(self, classes):
        self.data = classes
        self.date = datetime.now()
def writeCache(classes):
    """ Write the Class list to the cache file for later use """
    with open("classes.cache", "wb") as cache:
        pickle.dump(CachedData(classes), cache)
    return

def makeValidFilename(path):
    """
    Make the specified path valid on the current platform, i.e. remove all invalid characters
    Note: As it removes '/' only file/folder names are supported, no complete paths
    """
    system = platform.system()
    if system == "Windows":
        return re.sub("[<>:\"/\\|?*]", "", path).strip()
    else:
        return re.sub("[/]", "", path).strip()

def downloadClass(session, c):
    """ Download the Class from mybluprint.com """

    def scrapeEpisodes():
        """
        Scrape the episode URLs.
        As the URLs seem to go away after a while we query them only right before downloading.
        """
        for e in c.episodes:
            if e.url and e.vtt: continue
            try:
                url = f"https://api.mybluprint.com/m/videos/secure/episodes/{e.id}"
                print(f"  Fetching location of episode {e.title}: {url}")
                request = session.get(url)
                if request.status_code != 200:
                    print(f"!!! Downloading the episode info failed: {request.status_code}")
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
        """ Create the directory for the class, return the path """
        dir = f"{c.title} - {c.author}"
        dir = makeValidFilename(dir)
        if not os.path.exists(dir):
            os.makedirs(dir)
        return dir

    def writeClassInfo(dir):
        """ Write the info.json file in the Class' directory """
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
        """
        A generic function to download a file with progress indicator.
        We download to a temporary file and move at success to not leave behind incomplete files.
        """
        if os.path.exists(path): return
        tmp = "downloading.tmp"
        for repeat in range(3):
            try:
                with session.get(url, stream=True, timeout=10) as r:
                    if not r:
                        print(f"!!!  Error: {str(r)}")
                        return
                    total_length = int(r.headers.get('content-length')) if r.headers.get('content-length') else 0
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
            except requests.exceptions.Timeout:
                print(f"!!!  Timeout")
            except requests.exceptions.ConnectionError as e:
                print(f"!!!  Connection error: {e}")
            except requests.exceptions.HTTPError as e:
                print(f"!!!  HTTP error: {e}")
        print(f"!!!  Too many errors, not trying again")

    def downloadImage(dir):
        """ Download the image of the Class as Folder.jpg """
        print(f"  Downloading image")
        path = os.path.join(dir, "Folder.jpg")
        downloadFile(c.photo, path)
        return

    def downloadEpisode(i, classDir, e):
        """ Download the i-th Episode """

        def createDirectory():
            """ Create the directory for the episode """
            dir = os.path.join(classDir, makeValidFilename(f"{i} {e.title}"))
            if not os.path.exists(dir):
                os.makedirs(dir)
            return dir

        def writeEpisodeInfo(dir):
            """ Write the info.json file in the Episode's folder """
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
            """ Download the VTT file to be able to search for keywords later and directly get the timestamp in the Episode """
            print(f"    Downloading VTT")
            path = os.path.join(dir, makeValidFilename(f"{e.title}.vtt"))
            downloadFile(e.vtt, path)

        def downloadVideo(dir):
            """ Download the video file """
            print(f"    Downloading Video")
            path = os.path.join(dir, makeValidFilename(f"{e.title}.mp4"))
            downloadFile(e.url, path)

        print(f"  Downloading episode {i}: {e.title}")
        scrapeEpisodes()
        dir = createDirectory()
        writeEpisodeInfo(dir)
        downloadVTT(dir)
        downloadVideo(dir)
        return

    def downloadResource(i, classDir, r):
        """ Download the i-th Resource """
        print(f"  Downloading resource {i}: {r.title}")
        ext = os.path.splitext(urllib.parse.urlparse(r.url).path)[1] # extension from url (probably ".pdf")
        path = os.path.join(dir, makeValidFilename(f"{r.title}{ext}"))
        downloadFile(r.url, path)

    def writePlaylist(classDir):
        path = os.path.join(classDir, "playlist.m3u8")
        with open(path, "w") as m3u:
            m3u.write(    f"#EXTM3U\n")
            for i,e in enumerate(c.episodes):
                m3u.write(f"#EXTINF:-1,{e.title}\n")
                path = os.path.join(makeValidFilename(f"{i+1} {e.title}"), makeValidFilename(f"{e.title}.mp4"))
                m3u.write(f"{path}\n")

    print(f"Downloading class {c.title}")
    dir = createDirectory()
    writeClassInfo(dir)
    downloadImage(dir)
    for i,r in enumerate(c.resources):
        downloadResource(i+1, dir, r)
    for i,e in enumerate(c.episodes):
        downloadEpisode(i+1, dir, e)
    writePlaylist(dir)



session = requests.Session()
session.mount('https://', TLSAdapter()) # fixes WRONG_SIGNATURE_TYPE on https://course.mybluprint.com
for k,v in cookies.items(): # Add Cookies to be logged in
    session.cookies[k] = v


classes = loadCache()
if not classes: # no (valid) cache
    classes = scrapeData(session)
    writeCache(classes)

for c in classes:
    downloadClass(session, c)
    writeCache(classes)

