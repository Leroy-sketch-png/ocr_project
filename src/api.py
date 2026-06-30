import os
import tempfile
import json
from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import JSONResponse
import uvicorn
from .main import process_file

app = FastAPI(title="OCR Extraction API")

@app.post("/extract")
async def extract_fields_endpoint(file: UploadFile = File(...)):
    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported.")
    
    try:
        # Create a temporary file to save the uploaded PDF
        with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
            tmp_path = tmp.name
            content = await file.read()
            tmp.write(content)
            
        # Process the file using the core pipeline
        config_path = os.path.join(os.path.dirname(__file__), "field_config.yaml")
        results = process_file(tmp_path, config_path, optimization_mode=False)
        
        # Clean up
        os.remove(tmp_path)
        
        return JSONResponse(content=results)
        
    except Exception as e:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    uvicorn.run("src.api:app", host="0.0.0.0", port=8000, reload=True)
