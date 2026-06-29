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
    blob = bucket.blob(f'{query_date}_rentals_api_call')
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
    
    def dict_to_list(d):
        if not isinstance(d, dict): 
            return []
        return [{**value, 'event_date': key} for key, value in d.items()]
    
    df1 = df.drop(columns=['removedDate', 'listedDate', 'price'])
    gdf = gpd.GeoDataFrame(df1, geometry=gpd.points_from_xy(df1['longitude'], df1['latitude']), crs='EPSG:4326')

    gdf['history_list'] = gdf['history'].apply(dict_to_list)
    gdf_exploded = gdf.explode('history_list')
    history_expanded = pd.json_normalize(gdf_exploded['history_list']).set_index(gdf_exploded.index)
    final_gdf = pd.concat([gdf_exploded.drop(columns=['history', 'history_list']), history_expanded], axis=1)

    mask = ((final_gdf['longitude'] > -122.355) | (final_gdf['longitude'] < -122.517) | (final_gdf['latitude'] > 37.835) | (final_gdf['latitude'] < 37.704))
    if mask.any():
        geocodes = gpd.tools.geocode(final_gdf.loc[mask, 'formattedAddress'], provider='arcgis')
        final_gdf.loc[mask, 'geometry'] = geocodes['geometry']
    if mask.any:
        final_gdf = final_gdf.drop(final_gdf[mask].index)
    return final_gdf

def add_ids_to_gdf(gdf, neighborhoods, police_districts):
    gdf1 = gpd.sjoin(gdf, police_districts[['police_district_id', 'geometry']], how='left', predicate='within')
    gdf1.drop(columns=['index_right'], inplace=True)
    gdf2 = gpd.sjoin(gdf1, neighborhoods[['neighborhood_id', 'geometry']], how='left', predicate='within')

    gdf2['neighborhood_id'] = gdf2['neighborhood_id'].fillna(404)
    gdf2['police_district_id'] = gdf2['police_district_id'].fillna(404)

    return gdf2

def format_gdf_final_columns(gdf):
    gdf1 = gdf[['id', 'listedDate', 'removedDate', 'neighborhood_id', 'police_district_id', 
                'propertyType', 'bedrooms', 'bathrooms', 'squareFootage', 'status', 
                'price', 'formattedAddress', 'latitude', 'longitude', 'geometry']].copy()
    gdf1.columns = ['rentcast_id', 'listed_date', 'removed_date', 'neighborhood_id', 'police_district_id', 
                    'property_type', 'beds', 'baths', 'square_footage', 'status', 'price', 
                    'formatted_address', 'lat', 'long', 'geometry']
    
    gdf1['geometry'] = gdf1.geometry.to_wkt()

    timestamp_cols = ['listed_date', 'removed_date']
    for col in timestamp_cols:
        gdf1[col] = pd.to_datetime(gdf1[col]).dt.tz_convert('UTC')
    
    gdf1 = gdf1.astype(
    {'rentcast_id': str,
     'property_type': str,
     'beds': float,
     'baths': float,
     'square_footage': float,
     'status': str,
     'price': 'Int64',
     'formatted_address': str,
     'lat': float,
     'long': float})

    return gdf1

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
                ON T.rentcast_id = S.rentcast_id AND T.listed_date = S.listed_date
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
    table_name = 'rental_listings'
    staging_table = 'rental_listings__staging'

    final_cols = ['rentcast_id', 'listed_date', 'removed_date', 'neighborhood_id', 'police_district_id', 
                'property_type', 'beds', 'baths', 'square_footage', 'status', 'price', 
                'formatted_address', 'lat', 'long', 'geometry']

    backtick_cols = [f"`{col}`" for col in final_cols]
    update_set = ",".join([f'T.`{col}` = S.`{col}`' for col in final_cols[2:]])
    insert_cols = ",".join(backtick_cols)
    insert_values = ",".join([f'S.`{col}`' for col in final_cols])

    query_date = str(dt.date.today())
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
