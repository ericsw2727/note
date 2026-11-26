# doodle.py를 exe 하나로 묶는다. 결과물: dist\note.exe
python -m pip install -r requirements.txt
python tools\prepare_assets.py
python -m PyInstaller --onefile --noconsole --name note --icon assets\poodle\app-icon.ico --add-data "res;res" `
    --exclude-module tkinter --exclude-module numpy --exclude-module PIL doodle.py
Write-Host "Done: dist\note.exe"
