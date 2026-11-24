import os,re,json,urllib.request
from pathlib import Path
from urllib.parse import urlparse,parse_qs
from fastapi import FastAPI,HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel,Field

BASE=Path(__file__).parent
app=FastAPI(title='Olive English Lab V14')
app.mount('/static',StaticFiles(directory=BASE/'static'),name='static')
class Video(BaseModel):url:str=Field(max_length=500)
class Translation(BaseModel):sentences:list[str]=Field(min_length=1,max_length=1);context_before:str='';context_after:str=''
def video_id(url):
 p=urlparse(url.strip());host=(p.hostname or '').lower()
 if host in ('youtu.be','www.youtu.be'):v=p.path.strip('/').split('/')[0]
 elif host in ('youtube.com','www.youtube.com','m.youtube.com','music.youtube.com'):
  v=parse_qs(p.query).get('v',[''])[0] if p.path=='/watch' else (p.path.split('/')[2] if re.match(r'^/(shorts|live|embed)/',p.path) and len(p.path.split('/'))>2 else '')
 else:return None
 return v if re.fullmatch(r'[A-Za-z0-9_-]{11}',v) else None
@app.get('/api/version')
def version():return {'version':'V43','transcript':'normalized-continuous'}
@app.get('/')
def index():return FileResponse(BASE/'static/index.html')
@app.post('/api/transcript')
def transcript(body:Video):
 vid=video_id(body.url)
 if not vid:raise HTTPException(400,'올바른 YouTube 영상 링크를 입력해 주세요.')
 try:
  from youtube_transcript_api import YouTubeTranscriptApi
  from youtube_transcript_api._errors import TranscriptsDisabled,NoTranscriptFound,VideoUnavailable,IpBlocked,RequestBlocked
 except ImportError:
  raise HTTPException(503,'자막 모듈이 설치되지 않았어요. CMD에서 python -m pip install -r requirements.txt 를 실행해 주세요.')
 try:
  api=YouTubeTranscriptApi()
  # Prefer an English original; fall back to an available auto-generated English caption.
  try:
   captions=api.fetch(vid,languages=['en','en-US','en-GB'])
   language='en'
  except NoTranscriptFound:
   available=api.list(vid)
   chosen=None
   for item in available:
    if item.language_code.lower().startswith('en'):
     chosen=item;break
   if chosen is None:
    for item in available:
     if item.is_translatable:
      try:
       chosen=item.translate('en');break
      except Exception:pass
   if chosen is None:
    raise HTTPException(422,'이 영상에는 가져올 수 있는 영어 자막이나 영어로 번역 가능한 자막이 없어요. YouTube에서 스크립트를 복사해 붙여넣어 주세요.')
   captions=chosen.fetch()
   language=chosen.language_code
  # V28: Preserve the source caption text and ordering at import time.
  # Do not remove music cues, tags, punctuation, or merge/split segments here.
  items=[]
  for x in captions:
   text=str(x.text or '')
   if not text.strip():continue
   items.append({'text':text,'start':float(x.start),'duration':float(x.duration)})
  if not items:raise HTTPException(422,'자막 요청은 성공했지만 실제 자막 내용이 비어 있어요.')
  # Restore V14 import: one continuous transcript, no caption-boundary newlines.
  # Keep original caption segments separately for backup and timing.
  script=' '.join(re.sub(r'\s+', ' ', item['text']).strip() for item in items if item['text'] and not item['text'].lstrip().startswith('['))
  return {'videoId':vid,'script':script,'rawCaptions':items,'truncated':False,'captionCount':len(items),'language':language}

 except HTTPException:raise
 except TranscriptsDisabled:raise HTTPException(422,'영상에 자막이 비활성화되어 있어요. YouTube 스크립트를 직접 붙여넣어 주세요.')
 except VideoUnavailable:raise HTTPException(422,'영상을 사용할 수 없어요. 비공개·삭제·지역 제한 여부를 확인해 주세요.')
 except (IpBlocked,RequestBlocked):raise HTTPException(503,'YouTube가 이 컴퓨터의 자막 요청을 차단했어요. 잠시 후 다시 시도하거나 YouTube에서 스크립트를 복사해 주세요.')
 except NoTranscriptFound:raise HTTPException(422,'이 영상에서 사용할 수 있는 영어 자막을 찾지 못했어요. 영상의 자막 설정을 확인해 주세요.')
 except Exception as e:
  raise HTTPException(502,'YouTube 자막 요청 실패 ('+type(e).__name__+'). 인터넷 연결과 자막 제공 여부를 확인해 주세요. 직접 붙여넣기도 가능합니다.')

# Translation provider choices: local offline Argos, or a user-configured official Google Cloud Translation API.
# No scraping of consumer Google Translate / Papago websites and no silent paid calls.
def translate_argos(text):
 try:
  from argostranslate import translate
 except ImportError:raise HTTPException(503,'오프라인 번역이 설치되지 않았어요. README의 setup_translation.py를 실행해 주세요.')
 languages=translate.get_installed_languages()
 en=next((l for l in languages if l.code=='en'),None)
 ko=next((l for l in languages if l.code=='ko'),None)
 if not en or not ko:raise HTTPException(503,'영어→한국어 번역 모델이 없습니다. setup_translation.py를 실행해 주세요.')
 try:return en.get_translation(ko).translate(text)
 except Exception as e:raise HTTPException(503,'오프라인 번역에 실패했어요.') from e

def translate_google(text):
 key=os.getenv('GOOGLE_CLOUD_TRANSLATE_API_KEY','').strip()
 if not key:raise HTTPException(503,'Google Cloud Translation API 키가 설정되지 않았어요. 비용이 발생할 수 있습니다.')
 data=json.dumps({'q':text,'source':'en','target':'ko','format':'text'}).encode()
 req=urllib.request.Request('https://translation.googleapis.com/language/translate/v2?key='+key,data=data,headers={'Content-Type':'application/json'})
 try:
  with urllib.request.urlopen(req,timeout=15) as r:result=json.load(r)
  import html
  return html.unescape(result['data']['translations'][0]['translatedText'])
 except Exception as e:raise HTTPException(502,'Google Cloud 번역 요청에 실패했어요. API 설정과 사용량을 확인해 주세요.') from e

@app.get('/api/translation-status')
def translation_status():
 provider=os.getenv('TRANSLATION_PROVIDER','manual').lower()
 return {'provider':provider,'configured':bool(os.getenv('GOOGLE_CLOUD_TRANSLATE_API_KEY')) if provider=='google' else True,'notice':'Google Cloud API는 별도 과금될 수 있어요.' if provider=='google' else '기본값은 수동 Google 번역 확인 + 로컬 저장입니다. 자동 번역 API를 사용하지 않습니다.'}
@app.post('/api/translate')
def translate(body:Translation):
 text=body.sentences[0].strip()[:1000]
 if not text:raise HTTPException(400,'문장을 입력해 주세요.')
 provider=os.getenv('TRANSLATION_PROVIDER','manual').lower()
 if provider=='manual':raise HTTPException(409,'무료 Google 번역 웹사이트에서 번역을 확인한 후 프로그램에 붙여넣어 저장하세요. 자동 API 연결은 사용하지 않습니다.')
 if provider=='argos':ko=translate_argos(text)
 elif provider=='google':ko=translate_google(text)
 else:raise HTTPException(503,'지원하지 않는 번역 설정입니다.')
 return {'translations':[{'index':1,'ko':ko}],'provider':provider}
