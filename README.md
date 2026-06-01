# Tri-Model Smart Justice Framework — TMSJ

**Project ID:** UQU-DS-2025-F08  
**University:** Umm Al-Qura University — College of Computing, Data Science Department  
**Supervisor:** Dr. Olfat Mirza  
**Date:** June 2026

---

## Team Members

| Name | Student ID |
|------|-----------|
| Mayar Turki Alowaydhi | 443003550 |
| Reuf Bakr Alshrif | 443002428 |
| Jana Hesham Alhlis | 443002347 |
| Fajr Faisal Alzahrani | 44410657 |

---

## Project Overview

The **Tri-Model Smart Justice (TMSJ)** framework is an end-to-end Arabic Legal AI system designed to analyze Saudi commercial court decisions and predict judicial outcomes with explainability and transparency.

The framework addresses a critical gap in Arabic LegalTech by integrating three sequential AI models:

1. **Model A — Reader/Extractor:** Extracts structured legal entities from Saudi commercial judicial texts using Named Entity Recognition (NER) with AraBERTv0.2.
2. **Model B — Scenario Generator:** Generates structured Saudi legal scenarios based on the entities extracted by Model A, using AraGPT2-base.
3. **Model C — Predictor/Outcome:** Predicts judicial outcomes (Accepted / Rejected / Partially Accepted) with explainable reasoning using TF-IDF + LinearSVC + XAI techniques.

The project aligns with **Saudi Vision 2030** objectives for digital transformation and intelligent judicial technologies.

---

## Key Results

| Model | Task | Primary Metric | Score |
|-------|------|----------------|-------|
| Model A | Legal NER | F1-Score | **92.65%** |
| Model B | Scenario Generation | BLEU | **47.94%** / ROUGE-L **62.30%** |
| Model C | Judicial Prediction | Accuracy | **72.86%** |

---

## Dataset

The dataset was constructed from two official Saudi governmental sources:

- **4,677 judicial decisions** from the Saudi Ministry of Justice (`laws.moj.gov.sa`)
- **25 statutory laws** from the Bureau of Experts at the Council of Ministers (`laws.boe.gov.sa`)

---

## Installation

### Requirements

- Python 3.9+
- CUDA-capable GPU (recommended for Models A and B)

### Setup

```bash
git clone https://github.com/<your-org>/TMSJ.git
cd TMSJ
pip install -r requirements.txt
```

### Dependencies (`requirements.txt`)

```
numpy
pandas
torch
transformers
datasets
accelerate
evaluate
seqeval
rouge-score
sacrebleu
nltk
scikit-learn
joblib
tabulate
kagglehub
sentencepiece
matplotlib
```

---

## Running the Models

All three models run on Kaggle (GPU T4 x2 recommended) or locally with a compatible GPU.

### Model A — Legal Entity Extraction (NER)

Open and run `notebooks/TMSJ_Model_A.ipynb`.

Key configurations (set in `Config` class inside the notebook):

```python
MODEL_NAME   = "aubmindlab/bert-base-arabertv02"
MAX_LEN      = 512
RANDOM_SEED  = 42
NUM_EPOCHS   = 3
LEARNING_RATE = 2e-5
BATCH_SIZE   = 8
```

**Output:** Trained NER model saved to `/kaggle/working/model_a/final_model/`  
**Produces:** `model_b_input_clean_final.jsonl` — input file for Model B.

---

### Model B — Legal Scenario Generation

Open and run `notebooks/TMSJ_Model_B.ipynb`.

Key configurations:

```python
MODEL_NAME    = "aubmindlab/aragpt2-base"
MAX_SEQ_LEN   = 1024
LEARNING_RATE = 3e-5
BATCH_SIZE    = 1   # gradient_accumulation_steps=4 → effective batch=4
NUM_EPOCHS    = 3
```

**Input:** `model_b_input_clean_final.jsonl` (from Model A output)  
**Output:** `model_b_prompts_final.jsonl` and generated legal scenarios for Model C.

---

### Model C — Judicial Outcome Prediction

Open and run `notebooks/TMSJ_Model_C.ipynb`.

Key configurations:

```python
VECTORIZER  = TfidfVectorizer(max_features=30000, ngram_range=(1,3))
CLASSIFIER  = LinearSVC(class_weight='balanced', max_iter=5000)
RANDOM_SEED = 42
```

**Input:** `model_c_combined_train/val/test.jsonl` (real cases + validated Model B scenarios)  
**Output:** Judicial outcome predictions with explainability reports.

---

## Repository Structure

```
TMSJ/
│
├── Core_Legal_Datasets/
│   ├── Saudi Commercial Court Decisions
│   └── Saudi Legal Regulations
│
├── Model_Integration/
│   └── End-to-End TMSJ Pipeline
│
├── Data_Collection_and_Web_Scraping_Code.rar
├── Data_Preprocessing.ipynb
│
├── Model A – Legal Entity Extraction (NER).py
├── Model B – Legal Scenario Generation.py
├── Model C – Judicial Outcome Prediction and Explainability.py
│
├── requirements.txt
└── README.md
```

---

## Framework Architecture

```
Raw Saudi Legal Texts
        │
        ▼
┌─────────────────────────────┐
│  Model A — AraBERTv0.2 NER  │  → Extracts: case number, court, parties,
│  Legal Entity Extraction     │    dates, amounts, legal references
└─────────────────────────────┘
        │  Structured Entities (JSON)
        ▼
┌─────────────────────────────┐
│  Model B — AraGPT2-base     │  → Generates 7-section Saudi legal scenarios:
│  Scenario Generation         │    Summary, Claim, Response, Legal Issue,
└─────────────────────────────┘    Regulations, Reasoning, Expected Outcome
        │  Validated Legal Scenarios
        ▼
┌─────────────────────────────┐
│  Model C — TF-IDF + LinearSVC│  → Predicts: Accepted / Rejected / Partial
│  Explainable Prediction      │    + Feature importance + Confidence score
└─────────────────────────────┘    + Rule-based legal reasoning explanation
        │
        ▼
Explainable Judicial Outcome Prediction
```

---

## Reproducibility

To reproduce the TMSJ framework results, execute the project notebooks sequentially:

1. Data Preprocessing
2. Model A – Legal Entity Extraction
3. Model B – Legal Scenario Generation
4. Model C – Judicial Outcome Prediction and Explainability

All required dependencies are listed in `requirements.txt`.

---

## Ethical Note

All judicial case data was collected from publicly available official Saudi governmental portals. Party names in many cases are anonymized by the source portal; the framework uses `[PLAINTIFF]` and `[DEFENDANT]` placeholders where names are unavailable or masked to preserve privacy.

---



