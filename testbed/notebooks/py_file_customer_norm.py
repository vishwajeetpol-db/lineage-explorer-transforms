"""T1-3  spark_python_task source (a plain .py FILE, deliberately NOT a notebook).

Run by the job `bt-pyfile-customer-norm`. Exercises the resolver's
spark_python_task branch and the PySpark AST parser on a non-notebook artifact.

Expected transformation lineage for pyfile_customer_norm:
  full_name_upper = upper(concat(first_name, ' ', last_name))   [STRING_FN]
  email_domain    = split(email, '@')[1]                        [STRING_FN]
  tenure_days     = datediff(current_date(), signup_date)        [DATE_FN]
"""
import sys

from pyspark.sql import SparkSession
from pyspark.sql import functions as F

CATALOG = sys.argv[1] if len(sys.argv) > 1 else "__CATALOG__"
SCHEMA = sys.argv[2] if len(sys.argv) > 2 else "__SCHEMA__"

spark = SparkSession.builder.getOrCreate()

src = spark.table(f"{CATALOG}.silver.dim_customers")

out = src.select(
    F.col("customer_id"),
    F.upper(F.col("full_name")).alias("full_name_upper"),
    F.split(F.col("email"), "@").getItem(1).alias("email_domain"),
    F.datediff(F.current_date(), F.col("signup_date")).alias("tenure_days"),
    F.upper(F.col("country")).alias("country_code"),
)

out.write.mode("overwrite").option("overwriteSchema", "true").saveAsTable(
    f"{CATALOG}.{SCHEMA}.pyfile_customer_norm"
)
print(f"wrote {CATALOG}.{SCHEMA}.pyfile_customer_norm")
