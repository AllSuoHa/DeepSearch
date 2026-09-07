"""旧研究页兼容入口；实际操作已合并到统一对话工作台。"""

import streamlit as st

st.title("研究模式已合并到对话")
st.caption("在统一输入区选择“研究”，即可使用高级选项、三阶段生成和证据审校。")
st.page_link("app_pages/home.py", label="返回对话工作台", icon=":material/chat:")
