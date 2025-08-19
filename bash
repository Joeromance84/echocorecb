curl -X POST "http://localhost:8000/insights" \
     -H "Content-Type: application/json" \
     -d '{
        "queryId": "example-123",
        "timestamp": "2025-08-19T01:59:16Z",
        "data": {
            "temporal": {
                "series": [0.1, 0.2, 0.3, 0.4, 0.5]
            }
        }
     }'
