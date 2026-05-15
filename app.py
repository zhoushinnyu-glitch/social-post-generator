import streamlit as st
from fb_post_generator import process_url

st.set_page_config(
    page_title="Post Generator",
    page_icon="✍️"
)

password = st.text_input("Password", type="password")
if password != st.secrets["APP_PASSWORD"]:
    st.stop()

st.title("Social Post Generator")

url = st.text_input("Paste article URL")

col1, col2, col3 = st.columns(3)

with col1:
    jw_clicked = st.button("📘 JW Facebook", use_container_width=True)

with col2:
    pr_clicked = st.button("📣 PR Facebook", use_container_width=True)

with col3:
    xhs_clicked = st.button("📕 小红书", use_container_width=True)

if jw_clicked or pr_clicked or xhs_clicked:
    if not url:
        st.warning("Please enter a URL")
    else:
        if xhs_clicked:
            platform = "xiaohongshu"
            label = "小红书"
        elif pr_clicked:
            platform = "facebook_pr"
            label = "PR Facebook"
        else:
            platform = "facebook_jw"
            label = "JW Facebook"

        with st.spinner(f"Generating {label} post..."):
            result, image_files = process_url(url, platform=platform)

        st.text_area(
            f"{label} Post",
            result,
            height=450
        )

        if image_files:
            st.markdown("---")
            st.subheader("Images")

            cols = st.columns(3)
            for i, path in enumerate(image_files):
                with cols[i % 3]:
                    st.image(path)

            import zipfile, io
            zip_buffer = io.BytesIO()
            with zipfile.ZipFile(zip_buffer, "w") as zf:
                for path in image_files:
                    zf.write(path, arcname=path.split("/")[-1])
            zip_buffer.seek(0)

            st.download_button(
                label="⬇️ Download all images (.zip)",
                data=zip_buffer,
                file_name="images.zip",
                mime="application/zip"
            )
