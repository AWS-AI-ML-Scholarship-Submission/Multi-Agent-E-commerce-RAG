import re, time, boto3

REGION = "us-east-1"
ACCOUNT = "051080812655"
VECTOR_BUCKET = "udacity-agentcore-vectors-051080812655-e934d3e0"
POLICY_BUCKET = "udacity-agentcore-policy-docs-051080812655-e934d3e0"
ROLE_NAME = "udacity-agentcore-agentcore-role"
ROLE_ARN = f"arn:aws:iam::{ACCOUNT}:role/{ROLE_NAME}"
EMBED_ARN = f"arn:aws:bedrock:{REGION}::foundation-model/amazon.titan-embed-text-v2:0"

s3v = boto3.client("s3vectors", region_name=REGION)
iam = boto3.client("iam")
agent = boto3.client("bedrock-agent", region_name=REGION)

# 1. Checks
dim = s3v.get_index(vectorBucketName=VECTOR_BUCKET, indexName="returns-policy-index")["index"]["dimension"]
print("Index dimension:", dim)
trust = iam.get_role(RoleName=ROLE_NAME)["Role"]["AssumeRolePolicyDocument"]
print("Role trust:", trust)
if "bedrock.amazonaws.com" not in str(trust):
    raise SystemExit("Role does not trust bedrock.amazonaws.com - stop and paste this output to Claude.")

# 2. Create KBs
ids = {}
for domain in ["returns", "shipping", "warranty"]:
    kb = agent.create_knowledge_base(
        name=f"novamart-{domain}-policy-kb",
        roleArn=ROLE_ARN,
        knowledgeBaseConfiguration={
            "type": "VECTOR",
            "vectorKnowledgeBaseConfiguration": {
                "embeddingModelArn": EMBED_ARN,
                "embeddingModelConfiguration": {
                    "bedrockEmbeddingModelConfiguration": {"dimensions": dim, "embeddingDataType": "FLOAT32"}
                },
            },
        },
        storageConfiguration={
            "type": "S3_VECTORS",
            "s3VectorsConfiguration": {
                "indexArn": f"arn:aws:s3vectors:{REGION}:{ACCOUNT}:bucket/{VECTOR_BUCKET}/index/{domain}-policy-index"
            },
        },
    )["knowledgeBase"]
    kb_id = kb["knowledgeBaseId"]
    while agent.get_knowledge_base(knowledgeBaseId=kb_id)["knowledgeBase"]["status"] != "ACTIVE":
        time.sleep(3)
    ds_id = agent.create_data_source(
        knowledgeBaseId=kb_id,
        name=f"{domain}-policies",
        dataSourceConfiguration={
            "type": "S3",
            "s3Configuration": {
                "bucketArn": f"arn:aws:s3:::{POLICY_BUCKET}",
                "inclusionPrefixes": [f"policies/{domain}/"],
            },
        },
    )["dataSource"]["dataSourceId"]
    agent.start_ingestion_job(knowledgeBaseId=kb_id, dataSourceId=ds_id)
    ids[domain] = (kb_id, ds_id)
    print(f"{domain}: KB={kb_id} DS={ds_id} (sync started)")

# 3. Write .env in place
env = open(".env").read()
for domain in ids:
    key = f"{domain.upper()}_KB_ID"
    line = f"{key}={ids[domain][0]}"
    if re.search(rf"^{key}=.*$", env, re.M):
        env = re.sub(rf"^{key}=.*$", line, env, flags=re.M)
    else:
        env += f"\n{line}"
open(".env", "w").write(env)

# 4. Wait for syncs
for domain, (kb_id, ds_id) in ids.items():
    while True:
        job = agent.list_ingestion_jobs(knowledgeBaseId=kb_id, dataSourceId=ds_id)["ingestionJobSummaries"][0]
        print(domain, job["status"])
        if job["status"] in ("COMPLETE", "FAILED"):
            break
        time.sleep(10)