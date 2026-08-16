import hashlib
import json
import os
from pathlib import Path
import shutil
import time
import uuid
import zipfile

MIDI_EXTS={".mid",".midi"}
MEDIA_EXTS={".gif",".mp4",".webm",".avi",".mov",".mkv"}


class ImportStore:
    def __init__(self):
        self.base=Path(os.environ.get("LOCALAPPDATA",Path.home()))/"NTEAutoPiano"
        self.library=self.base/"library";self.state_file=self.base/"state.json"
        self.index_file=self.base/"library.json";self.library.mkdir(parents=True,exist_ok=True)
        self._migrate_last_state()

    @staticmethod
    def _hash(path):
        h=hashlib.sha256()
        with Path(path).open("rb") as f:
            for block in iter(lambda:f.read(1024*1024),b""):h.update(block)
        return h.hexdigest()

    def _read_index(self):
        try:
            items=json.loads(self.index_file.read_text(encoding="utf-8"))
            if not isinstance(items,list):return []
            return [x for x in items if Path(x.get("midi","")).exists()]
        except Exception:return []

    def _write_index(self,items):
        self.base.mkdir(parents=True,exist_ok=True)
        self.index_file.write_text(json.dumps(items,ensure_ascii=False,indent=2),encoding="utf-8")

    def _migrate_last_state(self):
        if self.index_file.exists():return
        try:
            data=json.loads(self.state_file.read_text(encoding="utf-8"))
            if Path(data.get("midi","")).exists():
                data.update({"id":uuid.uuid4().hex,"created":time.time(),"source_hash":None})
                self._write_index([data])
        except Exception:pass

    def list_entries(self):
        items=self._read_index();items.sort(key=lambda x:x.get("created",0),reverse=True);return items

    def _existing(self,source_hash):
        for item in self._read_index():
            if source_hash and item.get("source_hash")==source_hash:return item
        return None

    @staticmethod
    def _result(item):
        return item["midi"],item.get("media"),item.get("config"),item["display_name"]

    def import_file(self,source):
        source=Path(source);source_hash=self._hash(source)
        existing=self._existing(source_hash)
        if existing:return self._result(existing)
        if source.suffix.lower()==".zip":midi,media,config=self._import_zip(source)
        elif source.suffix.lower() in MIDI_EXTS:
            folder=self.library/uuid.uuid4().hex;folder.mkdir(parents=True)
            midi=folder/source.name;shutil.copy2(source,midi);media=None;config=None
        else:raise ValueError("仅支持 MIDI 或包含 MIDI 的 ZIP")
        item={"id":uuid.uuid4().hex,"source_hash":source_hash,"midi":str(midi),
              "media":str(media) if media else None,"config":str(config) if config else None,
              "display_name":source.name,"mode":None,"created":time.time()}
        items=self._read_index();items.append(item);self._write_index(items)
        return self._result(item)

    def _import_zip(self,source):
        folder=self.library/uuid.uuid4().hex;folder.mkdir(parents=True)
        midis=[];media=[];configs=[]
        with zipfile.ZipFile(source) as archive:
            for info in archive.infolist():
                if info.is_dir():continue
                source_name=Path(info.filename).name
                suffix=Path(source_name).suffix.lower()
                is_config=source_name.lower()=="config.json"
                if suffix not in MIDI_EXTS|MEDIA_EXTS and not is_config:continue
                if not source_name:continue
                target=folder/source_name
                with archive.open(info) as src,target.open("wb") as dst:shutil.copyfileobj(src,dst)
                if suffix in MIDI_EXTS:midis.append(target)
                elif suffix in MEDIA_EXTS:media.append(target)
                elif is_config:configs.append(target)
        if not midis:
            shutil.rmtree(folder,ignore_errors=True);raise ValueError("ZIP 内没有找到 MIDI 曲谱")
        midis.sort(key=lambda p:p.name.lower());media.sort(key=lambda p:p.name.lower())
        return midis[0],media[0] if media else None,configs[0] if configs else None

    def save(self,midi,media,config,display_name,mode):
        data={"midi":midi,"media":media,"config":config,"display_name":display_name,"mode":mode}
        self.state_file.write_text(json.dumps(data,ensure_ascii=False),encoding="utf-8")
        items=self._read_index();found=False
        for item in items:
            if item.get("midi")==midi:
                item.update(data);found=True;break
        if not found and Path(midi).exists():
            items.append({**data,"id":uuid.uuid4().hex,"source_hash":None,"created":time.time()})
        self._write_index(items)

    def load(self):
        try:
            data=json.loads(self.state_file.read_text(encoding="utf-8"))
            if not Path(data.get("midi","")).exists():return None
            for key in ("media","config"):
                if data.get(key) and not Path(data[key]).exists():data[key]=None
            return data
        except Exception:return None