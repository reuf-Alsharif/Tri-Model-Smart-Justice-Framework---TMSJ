# Data Preprocessing

## Overview

This module prepares Saudi legal documents for the Tri-Model Smart Justice Framework (TMSJ).

## Processing Pipeline

The preprocessing pipeline includes:

1. **Legal Baseline Preparation:** Parsing Saudi laws, normalizing legal articles, and filtering inactive articles.
2. **Text Cleaning:** Removing HTML tags, normalizing Arabic text, and standardizing legal terminology.
3. **Text Segmentation:** Dividing court judgments into structured sections, such as facts, reasoning, and verdicts.
4. **Legal Entity Extraction:** Identifying entities such as financial amounts, legal references, courts, and case parties using rule-based patterns.
5. **Tokenization:** Preparing text inputs using the AraBERT v0.2 tokenizer.
6. **Deduplication and Data Splitting:** Removing duplicate records and organizing data into training, validation, and test sets.
7. **Model Data Preparation:** Preparing structured inputs for Model A, Model B, and Model C.
8. **Quality Assurance:** Checking data structure and legal references.

## Technologies

- Python
- Pandas and NumPy
- Regular Expressions
- Scikit-learn
- Hugging Face Transformers
- AraBERT v0.2

## Data Privacy

The legal datasets and generated data files are not included in this public repository. This module documents the preprocessing workflow without distributing the underlying dataset.


