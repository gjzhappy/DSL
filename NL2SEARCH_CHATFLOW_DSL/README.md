# NL2SEARCH Dify Chatflows

Each business owns a dedicated directory. The numbered YAML fragments are the Git source of truth; the merged full YAML is generated locally for Dify import and is ignored by Git.

```text
NL2SEARCH_CHATFLOW_DSL/
├── PHONE_MODULE_JF_CHATFLOW/
│   ├── PHONE_MODULE_JF_CHATFLOW_00.yml
│   ├── PHONE_MODULE_JF_CHATFLOW_01.yml
│   ├── PHONE_MODULE_JF_CHATFLOW_02.yml
│   └── PHONE_MODULE_JF_CHATFLOW.yml                 # generated, gitignored
├── SI_DESIGN_CONSTRAINT_CHATFLOW/
│   ├── SI_DESIGN_CONSTRAINT_CHATFLOW_00.yml
│   ├── SI_DESIGN_CONSTRAINT_CHATFLOW_01.yml
│   ├── SI_DESIGN_CONSTRAINT_CHATFLOW_02.yml
│   └── SI_DESIGN_CONSTRAINT_CHATFLOW.yml             # generated, gitignored
├── JF_CAPACITOR_CHATFLOW/
│   ├── JF_CAPACITOR_CHATFLOW_00.yml
│   ├── JF_CAPACITOR_CHATFLOW_01.yml
│   ├── JF_CAPACITOR_CHATFLOW_02.yml
│   └── JF_CAPACITOR_CHATFLOW.yml                     # generated, gitignored
└── tools/
    ├── merge_chatflow_yml.py
    └── merge_business_chatflow.py
```

## Build / validate

Run from the repository root.

- PHONE: `python NL2SEARCH_CHATFLOW_DSL/tools/merge_chatflow_yml.py`
- SI + JF: `python NL2SEARCH_CHATFLOW_DSL/tools/merge_business_chatflow.py ALL --write`
- SI + JF regression: `python scripts/business_query_chatflow_regression.py ALL`
- PHONE graph check: `python scripts/check_phone_module_jf_dsl.py`
- PHONE regression: `python scripts/phone_module_jf_regression.py`

Use the generated full YAML under the matching business directory when importing into Dify. Do not edit generated files; update the numbered source fragments and regenerate.
