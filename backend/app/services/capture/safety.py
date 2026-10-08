import os,re,time,threading
from urllib.parse import urljoin,urlparse
from urllib.robotparser import RobotFileParser
import httpx
from backend.app.config import ORIGIN

ACTIONS={'goto','click','wait_for','scroll','hover','set_viewport','screenshot','measure'}
BLOCKED=re.compile(r'checkout|payment|place.?order|sign.?up|register|login|log-in|account',re.I)
LAST_LOAD: dict[str,float]={}
LOCK=threading.Lock()
def validate_selector(selector: str) -> str:
    if not isinstance(selector,str) or not 1<=len(selector)<=200 or not re.fullmatch(r'[a-zA-Z0-9_\-\[\]="\'\s.#:>]+',selector):raise ValueError('Selector must be a simple CSS selector; script, URLs and dynamic expressions are refused.')
    return selector
def validate_url(url: str) -> str:
    full=urljoin(ORIGIN+'/',url);parts=urlparse(full)
    if parts.scheme not in ('http','https') or parts.username or parts.password:raise ValueError('Only HTTP(S) URLs without credentials are allowed.')
    if BLOCKED.search(parts.path+'?'+parts.query):raise ValueError('Checkout, payment, account and login paths are prohibited, including in demo recipes.')
    local=urlparse(ORIGIN)
    if parts.netloc==local.netloc and parts.scheme==local.scheme:return full
    if os.getenv('LIVE_CAPTURE_ENABLED','false').lower()!='true':raise ValueError('Live capture is disabled. Only the built-in demo origin is allowed.')
    hosts={h.strip().lower() for h in os.getenv('CAPTURE_ALLOWED_HOSTS','').split(',') if h.strip()}
    if parts.hostname not in hosts:raise ValueError('Live host is not in CAPTURE_ALLOWED_HOSTS.')
    if parts.port not in (None,80,443):raise ValueError('Live capture supports standard HTTP(S) ports only.')
    return full
def user_agent() -> str:return 'VisualQABridge/0.1 (+contact '+os.getenv('CAPTURE_CONTACT_EMAIL','demo@example.invalid')+')'
def before_navigation(url: str) -> None:
    full=validate_url(url);parts=urlparse(full)
    if parts.netloc==urlparse(ORIGIN).netloc:return
    origin=f'{parts.scheme}://{parts.netloc}'
    with LOCK:
        delay=3-(time.monotonic()-LAST_LOAD.get(parts.netloc,0))
        if delay>0:time.sleep(delay)
        try:r=httpx.get(origin+'/robots.txt',headers={'User-Agent':user_agent()},timeout=10,follow_redirects=False)
        except httpx.HTTPError:raise ValueError('Cannot verify robots.txt; live capture refused.') from None
        if r.status_code==404:lines=[]
        elif r.status_code!=200:raise ValueError('robots.txt unavailable or restricted; live capture refused.')
        else:lines=r.text.splitlines()
        parser=RobotFileParser();parser.parse(lines)
        if lines and not parser.can_fetch(user_agent(),full):raise ValueError('robots.txt disallows this capture.')
        LAST_LOAD[parts.netloc]=time.monotonic()
def validate_recipe(steps: list,allow_cart: bool=False) -> list:
    if not steps or len(steps)>30:raise ValueError('A recipe needs 1–30 steps.')
    live=False
    for step in steps:
        if not isinstance(step,dict) or step.get('action') not in ACTIONS:raise ValueError('Recipe action is not allowed. Use the approved capture DSL.')
        if step['action']=='goto':
            if not step.get('url'):raise ValueError('goto requires a URL.')
            full=validate_url(step['url']);live=urlparse(full).netloc!=urlparse(ORIGIN).netloc
        if step['action'] in ('click','wait_for','hover','scroll','measure'):
            target=validate_selector(step.get('selector',''))
            if 'add-to-cart' in target and not allow_cart:raise ValueError('Cart interaction requires allow_cart_interaction: true.')
            if live and step['action']=='click' and target not in ('[data-testid=add-to-cart]','[data-testid=open-upgrade]','[data-testid=close-popup]','[data-testid=menu-toggle]'):raise ValueError('Live click refused: only explicitly approved read-only controls or add-to-cart are supported.')
        if step['action']=='screenshot':validate_selector(step.get('target','body'))
        if step['action']=='set_viewport':
            if not all(isinstance(step.get(k),int) and 320<=step[k]<=2560 for k in ('width','height')):raise ValueError('Viewport dimensions must be integers from 320 to 2560.')
    if steps[0]['action']!='goto':raise ValueError('The first recipe step must be goto.')
    return steps
