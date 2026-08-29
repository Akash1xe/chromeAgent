import pytest
from playwright.async_api import async_playwright


HTML = """
<!doctype html>
<html>
  <head>
    <meta charset="utf-8">
    <style>
      #hover-menu { display: none; }
      #hover-target:hover + #hover-menu { display: block; }
      #drop { width: 120px; height: 40px; border: 1px solid black; }
    </style>
  </head>
  <body>
    <input id="name" />
    <select id="city">
      <option>Delhi</option>
      <option>Noida</option>
    </select>
    <input id="check" type="checkbox" />
    <input id="radio" type="radio" name="choice" />

    <div id="hover-target">Hover me</div>
    <div id="hover-menu">Hover menu visible</div>

    <div id="drag" draggable="true">Drag me</div>
    <div id="drop">Drop here</div>

    <input id="file" type="file" />
    <button id="alert" onclick="alert('hello')">Alert</button>
    <button id="newtab" onclick="window.open('data:text/html,<title>Second Tab</title><p>tab two</p>', '_blank')">New tab</button>

    <iframe id="frame" srcdoc="<button id='inside'>Iframe Button</button><p id='frame-text'>Iframe text</p>"></iframe>

    <table id="table">
      <tr><th>Name</th><th>Value</th></tr>
      <tr><td>Alpha</td><td>42</td></tr>
    </table>

    <div id="dynamic"></div>
    <script>
      setTimeout(() => {
        document.querySelector('#dynamic').textContent = 'Loaded dynamically';
      }, 150);

      const drag = document.querySelector('#drag');
      const drop = document.querySelector('#drop');
      drag.addEventListener('dragstart', e => e.dataTransfer.setData('text/plain', 'dragged'));
      drop.addEventListener('dragover', e => e.preventDefault());
      drop.addEventListener('drop', e => {
        e.preventDefault();
        drop.textContent = 'dropped';
      });
    </script>
  </body>
</html>
"""


@pytest.mark.asyncio
async def test_human_browser_action_primitives(tmp_path):
    playwright = await async_playwright().start()
    browser = await playwright.chromium.launch(headless=True)

    try:
        context = await browser.new_context()
        page = await context.new_page()
        await page.set_content(HTML)

        # Type / select / checkbox / radio.
        await page.locator("#name").fill("Akash")
        await page.locator("#city").select_option(label="Noida")
        await page.locator("#check").check()
        await page.locator("#radio").check()

        assert await page.locator("#name").input_value() == "Akash"
        assert await page.locator("#city").input_value() == "Noida"
        assert await page.locator("#check").is_checked()
        assert await page.locator("#radio").is_checked()

        # Hover menus.
        await page.locator("#hover-target").hover()
        assert await page.locator("#hover-menu").is_visible()

        # Drag-and-drop fallback used by browser-use 0.13.8: browser JavaScript.
        await page.evaluate("""
            (() => {
              const source = document.querySelector('#drag');
              const target = document.querySelector('#drop');
              const data = new DataTransfer();
              source.dispatchEvent(new DragEvent('dragstart', {
                bubbles: true,
                dataTransfer: data,
              }));
              target.dispatchEvent(new DragEvent('dragover', {
                bubbles: true,
                cancelable: true,
                dataTransfer: data,
              }));
              target.dispatchEvent(new DragEvent('drop', {
                bubbles: true,
                cancelable: true,
                dataTransfer: data,
              }));
              source.dispatchEvent(new DragEvent('dragend', {
                bubbles: true,
                dataTransfer: data,
              }));
            })()
        """)
        assert await page.locator("#drop").inner_text() == "dropped"

        # File upload.
        upload = tmp_path / "upload.txt"
        upload.write_text("chrome agent upload", encoding="utf-8")
        await page.locator("#file").set_input_files(str(upload))
        assert await page.locator("#file").evaluate("(el) => el.files[0].name") == "upload.txt"

        # Browser dialog.
        dialogs = []

        async def handle_dialog(dialog):
            dialogs.append(dialog.message)
            await dialog.accept()

        page.on("dialog", handle_dialog)
        await page.locator("#alert").click()
        assert dialogs == ["hello"]

        # Iframe interaction.
        frame = page.frame_locator("#frame")
        await frame.locator("#inside").click()
        assert await frame.locator("#frame-text").inner_text() == "Iframe text"

        # Smart dynamic wait, no fixed sleep.
        await page.locator("#dynamic").wait_for(state="visible")
        await page.wait_for_function(
            "document.querySelector('#dynamic').textContent === 'Loaded dynamically'"
        )
        assert await page.locator("#dynamic").inner_text() == "Loaded dynamically"

        # Text/table extraction.
        rows = await page.locator("#table tr").all_inner_texts()
        assert rows[0] == "Name\tValue"
        assert rows[1] == "Alpha\t42"

        # Multi-tab open/switch/close.
        async with context.expect_page() as page_info:
            await page.locator("#newtab").click()

        second = await page_info.value
        await second.wait_for_load_state()
        assert await second.title() == "Second Tab"
        assert len(context.pages) == 2

        await second.close()
        assert len(context.pages) == 1

        # Keyboard and scroll primitives.
        await page.locator("#name").focus()
        await page.keyboard.press("Control+A")
        await page.keyboard.type("Replaced")
        assert await page.locator("#name").input_value() == "Replaced"

        await page.mouse.wheel(0, 500)

    finally:
        await browser.close()
        await playwright.stop()
