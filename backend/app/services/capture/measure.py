"""Trusted measurement template; no website or model code is ever evaluated."""

from typing import Any
from playwright.sync_api import Page
from backend.app.services.capture.safety import validate_selector

MEASURE = """roots => roots.flatMap(root => [root, ...root.querySelectorAll('*')].slice(0,250).map((e,index) => {
 const s=getComputedStyle(e),r=e.getBoundingClientRect();
 return {testid:e.dataset.testid||'',tag:e.tagName.toLowerCase(),element_index:index,
 font_size:parseFloat(s.fontSize),line_height:s.lineHeight==='normal'?null:parseFloat(s.lineHeight),
 letter_spacing:s.letterSpacing,padding:{top:parseFloat(s.paddingTop),right:parseFloat(s.paddingRight),bottom:parseFloat(s.paddingBottom),left:parseFloat(s.paddingLeft)},
 margin:{top:parseFloat(s.marginTop),right:parseFloat(s.marginRight),bottom:parseFloat(s.marginBottom),left:parseFloat(s.marginLeft)},
 bounding_box:{x:r.x,y:r.y,width:r.width,height:r.height},text_content:e.textContent.trim(),
 visible:s.display!=='none'&&s.visibility!=='hidden'&&r.width>0&&r.height>0,
 scrollHeight:e.scrollHeight,clientHeight:e.clientHeight,
 feature_id:e.dataset.featureId||'',order:Array.from(e.querySelectorAll('[data-feature-id]')).map(c=>c.dataset.featureId)};
}))"""


def measure(page: Page, selector: str) -> list[dict[str, Any]]:
    validate_selector(selector)
    locator = page.locator(selector)
    if locator.count() != 1:
        raise ValueError(f"Selector must identify one changed section: {selector}")
    return locator.evaluate_all(MEASURE)
