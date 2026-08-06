.PHONY: setup run seed test clean admin

setup:
	C:\Users\Lenovo\AppData\Local\Programs\Python\Python313\python.exe -m venv venv
	.\venv\Scripts\python.exe -m pip install -r requirements.txt

run:
	.\venv\Scripts\uvicorn.exe app.main:app --reload --host 0.0.0.0 --port 8000

seed:
	.\venv\Scripts\python.exe -m scripts.seed_data

admin:
	.\venv\Scripts\python.exe -m scripts.create_admin

test:
	.\venv\Scripts\pytest.exe tests/ -v

clean:
	rm -rf __pycache__ .pytest_cache *.db chroma_data

