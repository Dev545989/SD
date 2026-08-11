"""import pandas as pd 

df = pd.read_csv('dubizzle_vehicles.csv')
print(df.columns)
# del 

# check: sourceID, state, purpose, price, product, type, isSellerVerified, productScore, format, 
#        category, canApplyBadges
[{'id': 2, 'level': 0, 'externalID': '129', 'name': 'مركبات', 'name_l1': 'Vehicles', 'slug': 'vehicles', 'slug_l1': 'vehicles', 'roles': ['show_phone_number'], 'nameSingular': None, 'nameSingular_l1': None}, {'id': 54, 'level': 1, 'externalID': '23', 'name': 'سيارات للبيع', 'name_l1': 'Cars for Sale', 'slug': 'cars-for-sale', 'slug_l1': 'cars-for-sale', 'roles': ['show_phone_number', 'dpv_mosaic_images'], 'nameSingular': 'سيارات للبيع', 'nameSingular_l1': 'Cars for Sale'}]
same: 
id objectID
description	description_l1
slug	slug_l1
createdAt	
geo_point/ geography.lat	geography.lng
productInfo.expiresAt	activeProducts.featured_ad.expiresAt
activeProducts.featured_ad.appliedAt	
activeProducts.featured_ad.name.ar	activeProducts.featured_ad.name.en
extraFields.make
extraFields.model
	
    extraFields.mileage	extraFields.version	extraFields.interior	extraFields.new_used	extraFields.body_type	extraFields.ga_user_id	extraFields.price_type	extraFields.consumption	extraFields.transmission	extraFields.ga_session_id	extraFields.extra_features	extraFields.deliverable	extraFields.video	extraFields.featured_agency	extraFields.panorama	extraFields.seller_verified	extraFields.delivery	contactInfo.roles	contactInfo.name	coverPhoto.id	coverPhoto.externalID	coverPhoto.title	coverPhoto.orderIndex	coverPhoto.nimaScore	coverPhoto.main	location.lvl0.id	location.lvl0.level	location.lvl0.externalID	location.lvl0.name	location.lvl0.name_l1	location.lvl0.slug	location.lvl0.slug_l1	location.lvl1.id	location.lvl1.level	location.lvl1.externalID	location.lvl1.name	location.lvl1.name_l1	location.lvl1.slug	location.lvl1.slug_l1	location.lvl2.id	location.lvl2.level	location.lvl2.externalID	location.lvl2.name	location.lvl2.name_l1	location.lvl2.slug	location.lvl2.slug_l1	category.lvl0.id	category.lvl0.level	category.lvl0.externalID	category.lvl0.name	category.lvl0.name_l1	category.lvl0.slug	category.lvl0.slug_l1	category.lvl0.roles	category.lvl0.nameSingular	category.lvl0.nameSingular_l1	category.lvl1.id	category.lvl1.level	category.lvl1.externalID	category.lvl1.name	category.lvl1.name_l1	category.lvl1.slug	category.lvl1.slug_l1	category.lvl1.roles	category.lvl1.nameSingular	category.lvl1.nameSingular_l1	agency.id	agency.objectID	agency.name	agency.name_l1	agency.externalID	agency.product	agency.package	agency.packageRef	agency.productScore	agency.licenses	agency.logo.id	agency.logo.url	agency.slug	agency.slug_l1	agency.tr	agency.tier	agency.roles	agency.active	agency.createdAt	agency.commercialNumber	agency.shortNumber	agency.type	productInfo	activeProducts	extraFields.free-ad-commission
	142000	Presidential 	full-leather	used	5		price		2	1783958675	['1', '3', '21', '5', '31', '8', '32', '33', '34', '36', '13', '16', '17', '20', '37', '38', '22', '24', '27', '26', '4', '7', '9', '10', '11', '15', '18', '25', '40', '42', '43', '44', '45', '46']	no	no	no	no	no		['show_phone_number']	fahad	4705926	eae8577e-525d-45e8-9691-65b60229adca-8d16db54-6fa5-4164-93f3-57c8db90392a		0		TRUE	1	0	0-1	السعودية	Saudi Arabia	saudi-arabia	saudi-arabia	12	1	Jan-62	الرياض	Riyadh	riyadh	riyadh	80	2	Feb-74	الرياض	Riyadh	riyadh-2	riyadh-2	2	0	129	مركبات	Vehicles	vehicles	vehicles	['show_phone_number']			54	1	23	سيارات للبيع	Cars for Sale	cars-for-sale	cars-for-sale	['show_phone_number', 'dpv_mosaic_images']	سيارات للبيع	Cars for Sale																									

"""
from playwright.sync_api import sync_playwright


def get_contact_info(external_id):
    with sync_playwright() as p:

        browser = p.chromium.connect_over_cdp(
            "http://localhost:9222"
        )

        context = browser.contexts[0]
        page = context.pages[0]

        contact_data = {}

        def handle_response(response):
            if f"/api/listing/{external_id}/contactInfo/" in response.url:
                print("API URL:", response.url)
                print("Status:", response.status)

                if response.status == 200:
                    try:
                        data = response.json()
                        contact_data.update(data)
                        print("DATA:", data)
                    except Exception as e:
                        print("JSON Error:", e)

        page.on("response", handle_response)

        page.goto(
            "https://www.dubizzle.sa/en/vehicles/",
            wait_until="domcontentloaded"
        )

        page.wait_for_timeout(5000)

        listing_link = page.locator(
            f'a[href*="ID{external_id}"]'
        ).first

        print("Listings found:", listing_link.count())

        listing = listing_link.locator(
            'xpath=ancestor::*[.//*[@aria-label="Call"]][1]'
        )

        call_button = listing.locator(
            '[aria-label="Call"] button'
        ).first

        print("Call buttons found:", call_button.count())

        call_button.click()

        page.wait_for_timeout(5000)

        print("Contact data:", contact_data)

        return contact_data or None


data = get_contact_info("110708205")
print("Final result:", data)