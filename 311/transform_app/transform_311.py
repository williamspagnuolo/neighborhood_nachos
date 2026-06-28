import requests
import os
import datetime as dt
import json
from google.cloud import storage
from google.cloud import bigquery 
import geopandas as gpd
import pandas as pd
import numpy as np
from shapely import wkt


def pull_bucket_file(bucket_name: str, query_date:str):
    """query_date should be formatted as YYYY-MM-DD"""
    
    GSclient = storage.Client()
    bucket = GSclient.bucket(bucket_name)
    blob = bucket.blob(f'{query_date}_311_api_call')
    text = blob.download_as_text()
    
    return pd.DataFrame(json.loads(text))

def pull_bigquery_table(table_name: str):
    """Careful!! This pulls the ENTIRE table, so watch out!"""
    
    BQclient = bigquery.Client()
    query = f'''SELECT * 
            FROM `neighboorhood-nachos.neighborhood_livability_data.{table_name}`'''
    
    return BQclient.query(query).to_dataframe()

def format_bqtable_to_gdf(df):
    df['geometry'] = df['geometry'].apply(wkt.loads)
    return gpd.GeoDataFrame(df, geometry='geometry', crs="EPSG:4326")

def format_bucket_to_gdf(df):
    gdf = gpd.GeoDataFrame(df, geometry=gpd.points_from_xy(df['long'], df['lat']), crs='EPSG:4326')

    mask = (gdf['geometry'].is_empty) & (gdf['address'] != 'Not associated with a specific address')
    if mask.any():
        geocodes = gpd.tools.geocode(gdf.loc[mask, 'address'], provider='arcgis')
        gdf.loc[mask, 'geometry'] = geocodes['geometry']
    return gdf

def add_ids_to_gdf(gdf, neighborhoods, police_districts):
    gdf1 = gpd.sjoin(gdf, police_districts[['police_district_id', 'geometry']], how='left', predicate='within')
    gdf1.drop(columns=['index_right'], inplace=True)
    gdf2 = gpd.sjoin(gdf1, neighborhoods[['neighborhood_id', 'geometry']], how='left', predicate='within')

    mask = gdf2['geometry'].is_empty
    mask_neighbor = gdf2['neighborhood_id'].isna()
    mask_pd = gdf2['police_district_id'].isna()
    if mask.any():
        gdf2.loc[mask, 'police_district_id'] = 404
        gdf2.loc[mask, 'neighborhood_id'] = 404
    if mask_neighbor.any():
        lookup = neighborhoods.set_index('name')['neighborhood_id']
        gdf2.loc[mask_neighbor, 'neighborhood_id'] = gdf2.loc[mask_neighbor, 'neighborhoods_sffind_boundaries'].map(lookup)
    if mask_pd.any():
        lookup = police_districts.set_index('name')['police_district_id']
        gdf2.loc[mask_pd, 'police_district_id'] = gdf2.loc[mask_pd, 'police_district'].str.capitalize().map(lookup)
    
    gdf2['neighborhood_id'] = gdf2['neighborhood_id'].fillna(404)
    gdf2['police_district_id'] = gdf2['police_district_id'].fillna(404)

    return gdf2

def format_gdf_final_columns(gdf):
    gdf1 = gdf[['service_request_id', 'neighborhood_id', 'police_district_id', 'requested_datetime',
               'updated_datetime', 'closed_date', 'status_description', 'status_notes', 'agency_responsible', 'service_name',
               'service_subtype', 'service_details', 'supervisor_district', 'lat', 'long', 'geometry', 'source', 'media_url']].copy()
    gdf1.columns = ['311_incident_id', 'neighborhood_id', 'police_district_id', 'requested_datetime',
                   'updated_datetime', 'closed_datetime', 'status_description', 'status_notes',
                   'agency_responsible', 'service_name', 'service_subtype', 'service_details', 
                   'supervisor_district', 'lat', 'long', 'geometry', 'source', 'media_url']
    
    gdf1['geometry'] = gdf1.geometry.to_wkt()
    
    gdf1.loc[:, 'supervisor_district'] = gdf1['supervisor_district'].fillna('404')
    gdf1['supervisor_district'] = gdf1['supervisor_district'].astype(float)

    timestamp_cols = ['requested_datetime', 'updated_datetime', 'closed_datetime']
    for col in timestamp_cols:
        gdf1[col] = pd.to_datetime(gdf1[col]).dt.tz_localize('UTC')
    
    gdf1 = gdf1.drop_duplicates(subset=['311_incident_id'], keep='last')
    
    gdf1 = gdf1.astype(
    {'311_incident_id': 'int64',
     'status_description': str,
     'status_notes': str,
     'agency_responsible': str,
     'service_name': str,
     'service_subtype': str,
     'service_details': str,
     'supervisor_district': 'int64',
     'lat': float,
     'long': float,
     'source': str,
     'media_url': str})

    return gdf1

def upload_gdf_to_table(gdf, table_name):
    BQclient = bigquery.Client()
    job_config = bigquery.LoadJobConfig(write_disposition='WRITE_APPEND')
    job = BQclient.load_table_from_dataframe(gdf, table_name, job_config=job_config)
    
    return job.result()

def load_staging(gdf, staging_table):
    table = f'neighboorhood-nachos.neighborhood_livability_data.{staging_table}'
    BQclient = bigquery.Client()
    job_config = bigquery.LoadJobConfig(write_disposition='WRITE_TRUNCATE')
    job = BQclient.load_table_from_dataframe(gdf, table, job_config=job_config)
    return job.result()

def merge_staging_and_real(staging_table, table_name):
    merge_query = f'''
                MERGE `neighboorhood-nachos.neighborhood_livability_data.{table_name}` T
                USING `neighboorhood-nachos.neighborhood_livability_data.{staging_table}` S
                ON T.311_incident_id = S.311_incident_id
                WHEN MATCHED THEN
                    UPDATE SET {update_set}
                WHEN NOT MATCHED THEN
                    INSERT ({insert_cols})
                    VALUES ({insert_values})
                   '''
    BQclient = bigquery.Client()
    query_job = BQclient.query(merge_query)

    return query_job.result()

if __name__ == '__main__':
    bucket_name = os.environ.get("GCS_BUCKET_NAME")
    table_name = os.environ.get("BIG_QUERY_TABLE_NAME")
    staging_table = os.environ.get("BIG_QUERY_STAGING_NAME")

    final_cols = ['311_incident_id', 'neighborhood_id', 'police_district_id', 'requested_datetime',
                'updated_datetime', 'closed_datetime', 'status_description', 'status_notes',
                'agency_responsible', 'service_name', 'service_subtype', 'service_details', 
                'supervisor_district', 'lat', 'long', 'geometry', 'source', 'media_url']

    backtick_cols = [f"`{col}`" for col in final_cols]
    update_set = ",".join([f'T.`{col}` = S.`{col}`' for col in final_cols[1:]])
    insert_cols = ",".join(backtick_cols)
    insert_values = ",".join([f'S.`{col}`' for col in final_cols])

    query_date = str(dt.date.today() - dt.timedelta(days= 2))
    df = pull_bucket_file(bucket_name, query_date)

    df_neighborhoods = pull_bigquery_table('neighborhoods')
    gdf_neighborhoods = format_bqtable_to_gdf(df_neighborhoods)
    gdf_neighborhoods.columns = ['neighborhood_id', 'name', 'geometry']

    df_policedistricts = pull_bigquery_table('police_districts')
    gdf_policedistricts = format_bqtable_to_gdf(df_policedistricts)
    gdf_policedistricts.columns = ['police_district_id', 'name', 'geometry']

    gdf = format_bucket_to_gdf(df)
    gdf = add_ids_to_gdf(gdf, gdf_neighborhoods, gdf_policedistricts)
    gdf = format_gdf_final_columns(gdf)

    load_staging(gdf, staging_table)
    merge_staging_and_real(staging_table, table_name)
