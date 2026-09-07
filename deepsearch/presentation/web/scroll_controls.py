"""首页悬浮滚动控件。

Streamlit 没有原生的页面滚动命令，因此这里使用 Custom Components v2。
组件只包含仓库内的固定 HTML/CSS/JavaScript，不接收或渲染用户内容。
"""

from __future__ import annotations

import streamlit as st


_SCROLL_CONTROLS_HTML = """
<nav class="scroll-controls" aria-label="页面滚动">
  <button class="scroll-button" type="button" data-direction="top"
          aria-label="回到顶部" title="回到顶部" aria-hidden="true" tabindex="-1">
    <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <path d="M5 15.5 12 8l7 7.5M12 8v11M5 5h14" />
    </svg>
  </button>
  <button class="scroll-button" type="button" data-direction="bottom"
          aria-label="到达底部" title="到达底部" aria-hidden="true" tabindex="-1">
    <svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">
      <path d="m5 8.5 7 7.5 7-7.5M12 16V5M5 19h14" />
    </svg>
  </button>
</nav>
"""

_SCROLL_CONTROLS_CSS = """
:host {
  display: block;
  height: 0;
}

.scroll-controls {
  position: fixed;
  right: clamp(.75rem, 2vw, 1.5rem);
  bottom: calc(10.75rem + env(safe-area-inset-bottom, 0px));
  z-index: 1000;
  display: flex;
  flex-direction: column;
  gap: .45rem;
  pointer-events: none;
}

.scroll-button {
  width: 2.5rem;
  height: 2.5rem;
  display: grid;
  place-items: center;
  padding: 0;
  border: 1px solid var(--st-widget-border-color, var(--st-border-color));
  border-radius: var(--st-button-radius, 999px);
  background: color-mix(in srgb, var(--st-background-color, #fff) 92%, transparent);
  color: var(--st-text-color, #31333f);
  box-shadow: 0 4px 16px color-mix(in srgb, var(--st-text-color, #31333f) 14%, transparent);
  cursor: pointer;
  opacity: 0;
  visibility: hidden;
  pointer-events: none;
  transform: translateY(6px) scale(.96);
  backdrop-filter: blur(12px);
  transition:
    opacity 180ms ease,
    transform 180ms ease,
    visibility 0s linear 180ms,
    border-color 120ms ease,
    color 120ms ease;
}

.scroll-button.is-visible {
  opacity: 1;
  visibility: visible;
  pointer-events: auto;
  transform: translateY(0) scale(1);
  transition-delay: 0s;
}

.scroll-button:hover {
  border-color: var(--st-primary-color, #ff4b4b);
  color: var(--st-primary-color, #ff4b4b);
  transform: translateY(-1px);
}

.scroll-button:focus-visible {
  outline: 2px solid var(--st-primary-color, #ff4b4b);
  outline-offset: 2px;
}

.scroll-button svg {
  width: 1.15rem;
  height: 1.15rem;
  fill: none;
  stroke: currentColor;
  stroke-width: 1.8;
  stroke-linecap: round;
  stroke-linejoin: round;
}

@media (max-width: 768px) {
  .scroll-controls {
    right: .65rem;
    bottom: calc(12rem + env(safe-area-inset-bottom, 0px));
  }

  .scroll-button {
    width: 2.25rem;
    height: 2.25rem;
  }
}
"""

_SCROLL_CONTROLS_JS = """
export default function(component) {
  const { parentElement } = component;
  const topButton = parentElement.querySelector('[data-direction="top"]');
  const bottomButton = parentElement.querySelector('[data-direction="bottom"]');
  if (!topButton || !bottomButton) return;

  const isRootTarget = (target) =>
    target === document.scrollingElement ||
    target === document.documentElement ||
    target === document.body;

  const measure = (target) => {
    const rootTarget = isRootTarget(target);
    const viewportHeight = rootTarget ? window.innerHeight : target.clientHeight;
    const maximum = Math.max(0, target.scrollHeight - viewportHeight);
    const overflowY = rootTarget ? 'auto' : window.getComputedStyle(target).overflowY;
    return {
      target,
      rootTarget,
      maximum,
      current: rootTarget ? (window.scrollY || target.scrollTop) : target.scrollTop,
      scrollable: rootTarget || /^(auto|scroll|overlay)$/.test(overflowY),
    };
  };

  // Streamlit 的实际滚动节点会随版本和嵌入方式变化。不能直接采用第一个
  // selector 命中的节点；应从可信候选中选出真正可滚动且范围最大的节点。
  const scrollCandidates = () => Array.from(new Set([
    document.querySelector('[data-testid="stAppScrollToBottomContainer"]'),
    document.querySelector('[data-testid="stMain"]'),
    document.querySelector('section.stMain'),
    document.querySelector('section.main'),
    document.querySelector('[data-testid="stAppViewContainer"]'),
    document.scrollingElement,
    document.documentElement,
    document.body,
  ].filter(Boolean)));

  const resolveScrollTarget = () => {
    const measured = scrollCandidates().map(measure);
    const scrollable = measured.filter((item) => item.scrollable && item.maximum > 2);
    return (scrollable.length ? scrollable : measured)
      .sort((left, right) => right.maximum - left.maximum)[0];
  };

  const setVisible = (button, visible) => {
    button.classList.toggle('is-visible', visible);
    button.setAttribute('aria-hidden', visible ? 'false' : 'true');
    button.tabIndex = visible ? 0 : -1;
  };

  let controlsActive = false;
  let hideTimer = 0;
  let animationFrame = 0;
  let scrollAnimationFrame = 0;

  const updateAvailability = () => {
    const state = resolveScrollTarget();
    if (!state) return;
    const hasScrollableContent = state.maximum > 8;
    // 控件只在主内容区发生滚轮活动时出现；位于边界的无效方向不显示。
    setVisible(topButton, controlsActive && hasScrollableContent && state.current > 8);
    setVisible(
      bottomButton,
      controlsActive && hasScrollableContent && state.current < state.maximum - 8,
    );
  };

  const scheduleUpdate = () => {
    if (animationFrame) return;
    animationFrame = requestAnimationFrame(() => {
      animationFrame = 0;
      updateAvailability();
    });
  };

  const hideControls = () => {
    hideTimer = 0;
    controlsActive = false;
    scheduleUpdate();
  };

  const scheduleHide = () => {
    if (hideTimer) window.clearTimeout(hideTimer);
    hideTimer = window.setTimeout(hideControls, 1400);
  };

  const activateControls = () => {
    controlsActive = true;
    scheduleUpdate();
    scheduleHide();
  };

  const cancelScrollAnimation = () => {
    if (!scrollAnimationFrame) return;
    cancelAnimationFrame(scrollAnimationFrame);
    scrollAnimationFrame = 0;
  };

  const writePosition = (state, top) => {
    if (state.rootTarget) window.scrollTo({ top, behavior: 'auto' });
    else state.target.scrollTop = top;
  };

  const scrollTo = (direction) => {
    cancelScrollAnimation();
    const state = resolveScrollTarget();
    if (!state) return;
    const start = state.current;
    const destination = direction === 'top' ? 0 : state.maximum;
    const distance = destination - start;
    if (Math.abs(distance) < 1) return;

    // 使用可取消的短帧动画替代浏览器原生 smooth。滚轮事件会在默认滚动
    // 发生前取消此动画，因此第一下反向滚轮不会只用于“挣脱”平滑滚动。
    const startedAt = performance.now();
    const duration = Math.min(420, Math.max(220, Math.abs(distance) * .18));
    const tick = (now) => {
      const progress = Math.min(1, (now - startedAt) / duration);
      const eased = 1 - Math.pow(1 - progress, 3);
      writePosition(state, start + distance * eased);
      if (progress < 1) scrollAnimationFrame = requestAnimationFrame(tick);
      else {
        scrollAnimationFrame = 0;
        scheduleHide();
      }
    };
    scrollAnimationFrame = requestAnimationFrame(tick);
  };

  topButton.onclick = () => {
    activateControls();
    scrollTo('top');
  };
  bottomButton.onclick = () => {
    activateControls();
    scrollTo('bottom');
  };

  const handleWheel = (event) => {
    const state = resolveScrollTarget();
    if (!state) return;
    const eventPath = event.composedPath();
    const belongsToTarget = state.rootTarget || eventPath.includes(state.target);
    if (!belongsToTarget) return;
    cancelScrollAnimation();
    activateControls();
  };

  const keepVisible = () => {
    if (hideTimer) window.clearTimeout(hideTimer);
    hideTimer = 0;
  };

  // 捕获阶段的 document scroll 能覆盖根节点和任意候选容器；观察尺寸与
  // DOM 变化则让报告生成、折叠区展开等内容变化后也能及时重算按钮状态。
  document.addEventListener('wheel', handleWheel, { capture: true, passive: true });
  document.addEventListener('scroll', scheduleUpdate, { capture: true, passive: true });
  window.addEventListener('resize', scheduleUpdate, { passive: true });
  topButton.addEventListener('mouseenter', keepVisible);
  bottomButton.addEventListener('mouseenter', keepVisible);
  topButton.addEventListener('mouseleave', scheduleHide);
  bottomButton.addEventListener('mouseleave', scheduleHide);
  topButton.addEventListener('focus', keepVisible);
  bottomButton.addEventListener('focus', keepVisible);
  topButton.addEventListener('blur', scheduleHide);
  bottomButton.addEventListener('blur', scheduleHide);
  const resizeObserver = new ResizeObserver(scheduleUpdate);
  scrollCandidates().forEach((target) => resizeObserver.observe(target));
  const mutationObserver = new MutationObserver(scheduleUpdate);
  mutationObserver.observe(document.body, { childList: true, subtree: true });
  scheduleUpdate();

  return () => {
    topButton.onclick = null;
    bottomButton.onclick = null;
    document.removeEventListener('wheel', handleWheel, { capture: true });
    document.removeEventListener('scroll', scheduleUpdate, { capture: true });
    window.removeEventListener('resize', scheduleUpdate);
    topButton.removeEventListener('mouseenter', keepVisible);
    bottomButton.removeEventListener('mouseenter', keepVisible);
    topButton.removeEventListener('mouseleave', scheduleHide);
    bottomButton.removeEventListener('mouseleave', scheduleHide);
    topButton.removeEventListener('focus', keepVisible);
    bottomButton.removeEventListener('focus', keepVisible);
    topButton.removeEventListener('blur', scheduleHide);
    bottomButton.removeEventListener('blur', scheduleHide);
    resizeObserver.disconnect();
    mutationObserver.disconnect();
    if (hideTimer) window.clearTimeout(hideTimer);
    if (animationFrame) cancelAnimationFrame(animationFrame);
    cancelScrollAnimation();
  };
}
"""

def _register_scroll_controls():
    """通过公开 CCv2 API 注册组件并返回挂载函数。"""

    return st.components.v2.component(
        "deepsearch_scroll_controls",
        html=_SCROLL_CONTROLS_HTML,
        css=_SCROLL_CONTROLS_CSS,
        js=_SCROLL_CONTROLS_JS,
    )


# 正常的 Streamlit 进程只注册一次。独立 AppTest runtime 或开发热重载可能
# 重建组件管理器而保留 Python 模块缓存，render 中会针对这一情况安全重注册。
_SCROLL_CONTROLS = _register_scroll_controls()


def render_scroll_controls() -> None:
    """挂载避开底部输入框的回顶/到底按钮，不触发 Python rerun。"""

    global _SCROLL_CONTROLS
    try:
        _SCROLL_CONTROLS(key="home-scroll-controls", width="stretch", height=1)
    except ValueError as exc:
        if "is not registered" not in str(exc):
            raise
        _SCROLL_CONTROLS = _register_scroll_controls()
        _SCROLL_CONTROLS(key="home-scroll-controls", width="stretch", height=1)
