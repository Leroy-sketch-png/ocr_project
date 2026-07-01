import os
import tempfile

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse

from .main import process_file

app = FastAPI(title="OCR Extraction API")

_MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # 20 MB


@app.post("/extract")
async def extract_fields_endpoint(
    file: UploadFile = File(...),
    optimize: bool = True,
):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported.")

    content = await file.read()
    if len(content) > _MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"File too large. Maximum size is {_MAX_UPLOAD_BYTES // (1024*1024)} MB.",
        )

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            tmp_path = tmp.name
            tmp.write(content)

        config_path = os.path.join(os.path.dirname(__file__), "field_config.yaml")
        results = process_file(tmp_path, config_path, optimization_mode=optimize)
        return JSONResponse(content=results)

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.remove(tmp_path)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("src.api:app", host="0.0.0.0", port=8000, reload=True)
