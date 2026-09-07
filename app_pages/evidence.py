"""旧证据页兼容入口；证据详情现与每次研究结果放在一起。"""

import streamlit as st

st.title("证据详情已并入研究结果")
st.caption("完成一次研究后，展开“证据详情与审校”即可查看来源、质量分和检索轨迹。")
st.page_link("app_pages/home.py", label="返回对话工作台", icon=":material/chat:")
