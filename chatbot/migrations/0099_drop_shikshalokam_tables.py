from django.db import migrations

# The `shikshalokam` app has been removed entirely (code cleanup: not used by
# Saathi, tables confirmed empty in prod). Its own migration history can't run
# this drop since the app directory no longer exists, so this lives in `chatbot`
# (a surviving app) as a plain SQL drop instead — same pattern as
# 0097_drop_observability_tables.
#
# shikshalokam's base tables live in a dedicated "shikshalokam" Postgres schema;
# their simple_history historical tables do not follow that schema-qualification
# and live in the default schema as shikshalokam_historical<name>.
#
# IMPORTANT: do NOT drop the "shikshalokam" schema itself. The kept, live
# `chatbot.BotVernacular` model also stores its table there
# (db_table = 'shikshalokam"."bot_vernacular'), unrelated to the removed
# shikshalokam app — an earlier version of this migration did
# `DROP SCHEMA "shikshalokam" CASCADE` and took BotVernacular's table down with
# it, breaking the admin panel. Only drop the specific shikshalokam-app tables
# by name; leave the schema and everything else in it alone.
DROP_TABLES_SQL = """
DROP TABLE IF EXISTS "shikshalokam"."project" CASCADE;
DROP TABLE IF EXISTS "shikshalokam"."task" CASCADE;
DROP TABLE IF EXISTS "shikshalokam"."evidence" CASCADE;
DROP TABLE IF EXISTS "shikshalokam"."learning_resource" CASCADE;
DROP TABLE IF EXISTS "shikshalokam"."project_wishlist" CASCADE;
DROP TABLE IF EXISTS "shikshalokam"."category" CASCADE;
DROP TABLE IF EXISTS "shikshalokam"."project_template" CASCADE;
DROP TABLE IF EXISTS "shikshalokam"."project_vernacular" CASCADE;

DROP TABLE IF EXISTS shikshalokam_historicalproject CASCADE;
DROP TABLE IF EXISTS shikshalokam_historicaltask CASCADE;
DROP TABLE IF EXISTS shikshalokam_historicalevidence CASCADE;
DROP TABLE IF EXISTS shikshalokam_historicallearningresources CASCADE;
DROP TABLE IF EXISTS shikshalokam_historicalprojectwishlist CASCADE;
DROP TABLE IF EXISTS shikshalokam_historicalcategory CASCADE;
DROP TABLE IF EXISTS shikshalokam_historicalprojecttemplate CASCADE;
DROP TABLE IF EXISTS shikshalokam_historicalprojectvernacular CASCADE;
"""


class Migration(migrations.Migration):

    dependencies = [
        ('chatbot', '0098_remove_story_media_theme_i18n_models'),
    ]

    operations = [
        migrations.RunSQL(
            sql=DROP_TABLES_SQL,
            reverse_sql=migrations.RunSQL.noop,
        ),
    ]
