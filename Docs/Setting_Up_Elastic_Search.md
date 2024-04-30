# ElasticsearchDB Configuration


1. **Create ElasticsearchDB Account**: Sign up for an Elasticsearch service. You can choose between a self-managed installation or a cloud-based service like Elastic Cloud.
   
2. **Access Elasticsearch Dashboard**: Once your account is created and you've logged in, navigate to the Elasticsearch dashboard.

3. **Open Dev Tools**: In the Elasticsearch dashboard, locate and click on the "Dev Tools" to access the Console.

4.  **Create 'ikms-assistants' Index**: In the Console, execute the following command to create the `ikms-assistants` index with the specified settings and mappings:

```json
PUT /ikms-assistants
{
    "settings": {
        "number_of_shards": 1
    },
    "mappings": {
        "properties": {
            "Prompt": {
                "type": "text"
            },
            "assistant_id": {
                "type": "text"
            },
            "assistant_name": {
                "type": "text",
                "fields": {
                    "keyword": {
                        "type": "keyword",
                        "ignore_above": 256
                    }
                }
            }
        }
    }
}
```

5. **Create 'meta-summary-registry' Index**: In the Console, execute the following command to create the `meta-summary-registry` index with the specified settings and mappings:

```json
PUT /meta-summary-registry
{
  "settings": {
    "number_of_shards": 1
  },
   "mappings": {
      "properties": {
        "Audio Source": {
          "type": "text"
        },
        "Data Type": {
          "type": "text"
        },
        "Document Description": {
          "type": "nested",
          "properties": {
            "Description": {
              "type": "text"
            },
            "Sections": {
              "type": "nested",
              "properties": {
                "Description": {
                  "type": "text"
                },
                "Keywords": {
                  "type": "keyword"
                },
                "Title": {
                  "type": "text"
                }
              }
            }
          }
        },
        "Document Name": {
          "type": "text"
        },
        "Document Source": {
          "type": "text"
        },
        "Metadata": {
          "type": "nested",
          "properties": {
            "Author": {
              "type": "text"
            },
            "Complexity": {
              "type": "long"
            },
            "Complexity ": {
              "type": "integer"
            },
            "DOC_Keywords": {
              "type": "keyword"
            },
            "Domain": {
              "type": "text",
              "fields": {
                "keyword": {
                  "type": "keyword",
                  "ignore_above": 256
                }
              }
            },
            "Domain ": {
              "type": "text"
            },
            "Security": {
              "type": "text"
            },
            "Target Audience": {
              "type": "text"
            },
            "Type": {
              "type": "text"
            }
          }
        },
        "Video Id": {
          "type": "text"
        },
        "Video Source": {
          "type": "text"
        }
      }
    }
}
```

5. **Verify Index Creation**: Ensure that the index is created without errors by checking the output message in the Console. 
