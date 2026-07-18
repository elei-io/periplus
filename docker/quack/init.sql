CALL quack_serve(
    'quack:0.0.0.0:9494',
    token = getenv('ATLAS_QUACK_TOKEN'),
    allow_other_hostname = true
);
