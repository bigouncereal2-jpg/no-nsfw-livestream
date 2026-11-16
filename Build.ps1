$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot

function Assert-Checksum($File, $Expected) {
    if ((Get-FileHash -LiteralPath $File -Algorithm SHA256).Hash -ne $Expected) {
        throw "Unexpected checksum for $File"
    }
}

py -3.12 -m venv .venv
if ($LASTEXITCODE -ne 0) { throw 'Python 3.12 is required.' }
$python = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
& $python -m pip install -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'Could not install the build dependencies.' }

if (!(Test-Path -LiteralPath 'image-safety.onnx')) {
    curl.exe -fLsS 'https://huggingface.co/OwenElliott/image-safety-classifier-xs/resolve/54f4560bd9c5ee92d45dc30418a8f8680e80de6d/onnx/image-safety-classifier-xs.onnx' -o image-safety.onnx
    if ($LASTEXITCODE -ne 0) { throw 'Could not retrieve the image safety model.' }
}
if (!(Test-Path -LiteralPath 'nudity.onnx')) {
    & $python -m pip download --no-deps --dest .venv 'nudenet==3.4.2'
    if ($LASTEXITCODE -ne 0) { throw 'Could not retrieve NudeNet.' }
    & $python -m zipfile -e .venv\nudenet-3.4.2-py3-none-any.whl .venv\nudenet-upstream
    if ($LASTEXITCODE -ne 0) { throw 'Could not extract NudeNet.' }
    Copy-Item -LiteralPath .venv\nudenet-upstream\nudenet\320n.onnx -Destination nudity.onnx
}
Assert-Checksum 'image-safety.onnx' '8C28C49D9075F3AD15EBDC2961F02D5B3F99BE944815B848B49C9F0E6F3FB689'
Assert-Checksum 'nudity.onnx' 'C15D8273ADAD2D0A92F014CC69AB2D6C311A06777A55545F2C4EB46F51911F0F'

& $python -m PyInstaller --noconfirm --onefile --windowed --name 'Stream Failsafe' --add-data 'source.html:.' --add-data 'image-safety.onnx:.' --add-data 'nudity.onnx:.' app.py
if ($LASTEXITCODE -ne 0) { throw 'Could not build Stream Failsafe.' }
