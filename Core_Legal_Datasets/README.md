# Core Legal Datasets

This directory contains the core legal datasets used in the Tri-Model Smart Justice Framework (TMSJ).

The datasets were collected from official Saudi governmental legal sources and serve as the primary knowledge base for all three models within the framework.

## Data Sources

### Saudi Ministry of Justice

Commercial court decisions collected from official Saudi judicial resources.

Contents:

* 4,677 Saudi commercial court decisions

File:

* `Saudi_Commercial_Cases_Dataset_Link.txt`

### Bureau of Experts at the Council of Ministers

Saudi laws and regulations collected from official legal publications.

Contents:

* 25 Saudi legal regulations and statutory laws

Examples include:

* Evidence Law
* Bankruptcy Law
* Commercial Courts Law
* Arbitration Law
* Companies Law
* Commercial Register Law
* Trademarks Law
* Labor Law
* Enforcement Law
* Commercial Fraud Law

and other Saudi legal regulations relevant to commercial judicial cases.

## Purpose

These datasets provide the legal foundation for the TMSJ framework:

* Model A uses the judicial decisions for legal entity extraction.
* Model B utilizes extracted entities and legal references for scenario generation.
* Model C uses validated legal scenarios and legal knowledge for judicial outcome prediction.

## Data Format

The legal regulations are stored in JSON format.

The commercial court decisions are provided through the dataset access link included in:

`Saudi_Commercial_Cases_Dataset_Link.txt`

## Total Dataset Size

* 4,677 Commercial Court Decisions
* 25 Saudi Legal Regulations

Total Legal Corpus:

* 4,702 Legal Documents

These datasets were used throughout data preprocessing, model training, validation, and evaluation stages of the TMSJ framework.
