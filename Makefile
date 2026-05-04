.PHONY: build clean

build:
	uv sync
	uv add --dev pyinstaller
	uv run --group dev python -m PyInstaller --onefile --name kubebox \
		--add-data "streamlit_app.py:." \
		--add-data "core:core" \
		main.py

clean:
	rm -rf build dist
