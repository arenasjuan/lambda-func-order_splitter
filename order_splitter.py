import json
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
import config

sku_to_pouches = {
    'SKU1': 1,
    'SKU2': 2,
    'SKU3': 3,
    # ... more SKUs
}

def lambda_handler(event, context):
    print("Lambda handler invoked")

    global session
    session = requests.Session()
    session.headers.update({
        "Content-Type": "application/json",
        "Authorization": f"Basic {config.encoded_auth_string}"
    })

    # Extract webhook payload
    payload = json.loads(event["body"])
    # Modify the resource_url to includeShipmentItems=True to access the order items
    response = session.get(payload['resource_url'][:-5] + "True")
    data = response.json()
    shipments = data['shipments']
    print(f"Number of shipments: {len(shipments)}")

    with ThreadPoolExecutor(max_workers=8) as executor:
        futures = [executor.submit(process_order, shipment, session) for shipment in shipments]
        for future in as_completed(futures):
            future.result()

    return {
        'statusCode': 200,
        'body': json.dumps('Lambda function executed successfully')
    }

def process_order(shipment):
    order = shipment['order']  # Assuming 'order' contains the order information

    if order_split_required(order):
        original_order, child_orders = prepare_split_data(order)
        original_order_id = original_order['orderId']

        # Update the original order with reduced quantities
        update_original_order(session, original_order_id, original_order)

        # Create new child orders
        for child_order in child_orders:
            create_child_order(session, child_order)

def update_original_order(order_id, order_data):
    url = f'https://ssapi.shipstation.com/orders/{order_id}'
    response = session.put(url, json=order_data)

    if response.status_code == 200:
        print(f"Original order {order_id} updated successfully")
    else:
        print(f"Error updating original order {order_id}: {response.text}")

def create_child_order(child_order):
    url = 'https://ssapi.shipstation.com/orders/createorder'
    response = session.post(url, json=child_order)

    if response.status_code == 200:
        print("Child order created successfully")
    else:
        print(f"Error creating child order: {response.text}")

# Include the order_split_required and prepare_split_data functions here
