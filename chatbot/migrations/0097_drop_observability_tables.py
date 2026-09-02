from django.db import migrations

# The `observability` app has been removed entirely (code cleanup: not used by
# Saathi, tables confirmed empty in prod). Its own migration history can't run
# this drop since the app directory no longer exists, so this lives in `chatbot`
# (a surviving app) as a plain SQL drop instead.
DROP_TABLES_SQL = """
DROP TABLE IF EXISTS observability_botruntestcasemap CASCADE;
DROP TABLE IF EXISTS observability_historicalbotruntestcasemap CASCADE;
DROP TABLE IF EXISTS observability_companybottcrun CASCADE;
DROP TABLE IF EXISTS observability_historicalcompanybottcrun CASCADE;
DROP TABLE IF EXISTS observability_companybottestcases CASCADE;
DROP TABLE IF EXISTS observability_historicalcompanybottestcases CASCADE;
DROP TABLE IF EXISTS observability_tcbotrunmetrics CASCADE;
"""


class Migration(migrations.Migration):

    dependencies = [
        ('chatbot', '0096_seed_flow_languages'),
    ]

    operations = [
        migrations.RunSQL(
            sql=DROP_TABLES_SQL,
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
