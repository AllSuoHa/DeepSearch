"""共享的信息类型内联编辑器与胶囊触发器状态。"""

from __future__ import annotations

from collections.abc import Sequence

import streamlit as st

from deepsearch.infrastructure.config import (
    Settings,
    normalize_custom_information_types,
    save_settings,
)


def request_information_type_editor(widget_key: str, editor_key: str) -> None:
    """消费加号胶囊的选中状态，并展开当前位置的添加表单。"""

    if not st.session_state.get(widget_key):
        return
    # 回调先于页面重绘执行，此时可以安全清空单选胶囊；加号不会
    # 保持选中，输入表单则通过独立状态留在当前控件组内。
    st.session_state[widget_key] = None
    st.session_state[editor_key] = True


def render_information_type_editor(
    settings: Settings,
    *,
    editor_key: str,
    form_key: str,
    existing_options: Sequence[str],
    success_message: str,
) -> None:
    """在调用位置渲染紧凑表单，并添加或取消一个信息类型。"""

    with st.form(form_key, border=False):
        with st.container(
            horizontal=True,
            vertical_alignment="bottom",
            gap="xsmall",
            key=f"{form_key}-row",
        ):
            new_information_type = st.text_input(
                "类型名称",
                max_chars=20,
                placeholder="输入类型名称",
                label_visibility="collapsed",
                key=f"{form_key}-name",
            )
            submitted = st.form_submit_button(
                "添加",
                type="primary",
                icon=":material/add:",
            )
            cancelled = st.form_submit_button(
                "取消",
                icon=":material/close:",
            )
    if cancelled:
        st.session_state[editor_key] = False
        st.rerun()
    if not submitted:
        return

    normalized_label = " ".join(new_information_type.split())
    existing_labels = {item.casefold() for item in existing_options}
    updated_types = normalize_custom_information_types((
        *settings.custom_information_types,
        normalized_label,
    ))
    if not normalized_label:
        st.error("请输入信息类型名称。", icon=":material/error:")
    elif normalized_label.casefold() in existing_labels:
        st.info("该类型已经存在。")
    elif updated_types == settings.custom_information_types:
        st.info("该类型已存在，或名称超过 20 个字符。")
    else:
        settings.custom_information_types = updated_types
        save_settings(settings)
        st.session_state[editor_key] = False
        st.toast(success_message, icon=":material/check_circle:")
        st.rerun()
