**European Economic Area (EEA) developers**

> [!NOTE]
> If your billing address is in the European Economic Area, effective on 8 July 2025, the [Google Maps Platform EEA Terms of Service](https://cloud.google.com/terms/maps-platform/eea) will apply to your use of the Services. Functionality varies by region. [Learn more](https://developers.google.com/maps/comms/eea/faq).

*Geocoding* translates an address into a location on a map. When
you geocode an address, the response contains the:

- [Place ID](https://developers.google.com/maps/documentation/places/web-service/place-id) of the location
- Latitude and longitude coordinates of the location
- [Plus Code](https://maps.google.com/pluscodes/) of the location
- Expanded address details

## Geocode request

A
[geocode request](https://developers.google.com/maps/documentation/geocoding/reference/rest/v4/geocode.address)
is an HTTP GET request. You can specify the address as an [unstructured
string](https://developers.google.com/maps/documentation/geocoding/geocoding#pass_an_unstructured_address_string):

```
https://geocode.googleapis.com/v4/geocode/address/ADDRESS_STRING
```

Or as a [structured](https://developers.google.com/maps/documentation/geocoding/geocoding#pass_a_structured_address) set of address components
represented by query parameters:

```
https://geocode.googleapis.com/v4/geocode/address?STRUCTURED_ADDRESS
```

You typically use the structured format when processing address components
captured in an HTML form.

Pass all other parameters as URL parameters or, for parameters such as the API
key and field mask, in headers as part of the GET request.

> [!NOTE]
> **Note:** URLs must be [properly encoded](https://developers.google.com/maps/documentation/geocoding/web-service-best-practices#BuildingURLs) to be valid and are limited to 4096 characters for all web services. Be aware of this limit when constructing your URLs. Note that different browsers, proxies, and servers may have different URL character limits as well.

### Pass an unstructured address string

An unstructured address is an address formatted as a string or a Plus Code.
Address geocoding does not resolve latitude and
longitude coordinates, or other unstructured strings that don't represent an
address or a Plus Code. Requests using such strings are not supported and may
lead to error responses or unspecified behavior. Examples of unsupported queries
include the following:

| Query type | Example |
|---|---|
| Latitude and longitude coordinates. Use [reverse geocoding](https://developers.google.com/maps/documentation/geocoding/reverse-geocoding) instead. | *"37.422131,-122.084801"* |
| Too many concepts or constraints, such as the names of multiple places, roads, or cities in a single query | *"Market Street San Francisco San Jose Airport"* |
| Postal address elements not represented on Google Maps | *"C/O John Smith 123 Main Street"* *"P.O. Box 13 San Francisco"* |
| Names of businesses, chains, or categories combined with locations where these entities are not available | *"Tesco near Dallas, Texas"* |
| Ambiguous queries with multiple interpretations | *"Charger drop-off"* |
| Historical names no longer in use | *"Middlesex United Kingdom"* |
| Non-geospatial elements or intent | *"How many boats are in Ventura Harbor?"* |
| Unofficial or vanity names | *"The Jenga"* *"The Helter Skelter"* |

For example, the following example passes the URL encoded address string "1600
Amphitheatre Parkway, Mountain View, CA":

```html
https://geocode.googleapis.com/v4/geocode/address/1600+Amphitheatre+Parkway,+Mountain+View,+CA?key=API_KEY
```

Notice that the "+" character in the URL is converted to a space.

You can also make the request using a curl command:

```
curl -H "X-Goog-Api-Key: API_KEY" \
"https://geocode.googleapis.com/v4/geocode/address/1600+Amphitheatre+Parkway,+Mountain+View,+CA"
```

Addresses can contain many types of special characters. For example, a "/" as in
"7/1 King St, Concord West". URL encode the "/" as `%2F`:

```html
https://geocode.googleapis.com/v4/geocode/address/7%2F1+King+St,+Concord+West
?key=API_KEY
```

Another common example is the "#" character, as in
"9500 W Bryn Mawr Ave #650, Rosemont". URL encode the "#" as `%2FE`:

```html
https://geocode.googleapis.com/v4/geocode/address/9500+W+Bryn+Mawr+Ave+%23650,+Rosemont?key=API_KEY
```

In the next example, you specify an unstructured address string as the
Plus Code `849VCWC8+R4`. Ensure that you URL-encode the "+" character as `%2B`:

```
https://geocode.googleapis.com/v4/geocode/address/849VCWC8%2BR4?key=API_KEY
```

### Pass a structured address

Specify a structured address by using the `address` query parameter, of type
[`PostalAddress`](https://developers.google.com/maps/documentation/geocoding/reference/rest/Shared.Types/PostalAddress).
The `PostalAddress` object lets you specify some or all address components in
the request as individual query parameters.

For example, to specify only the zip code of the address you use
`PostalAddress.postalCode`:

```
https://geocode.googleapis.com/v4/geocode/address?address.postalCode=01062&key=API_KEY
```

To specify multiple address components, such as for address components captured
in an HTML form, use multiple query parameters:

```
https://geocode.googleapis.com/v4/geocode/address?address.addressLines=1600+Amphithreater+Pkwy&address.locality=Mountain+View&address.administrativeArea=CA&key=API_KEY
```

### Use OAuth to make a request

Geocoding API v4 supports [OAuth
2.0](https://developers.google.com/maps/documentation/geocoding/oauth-token) for authentication. To use OAuth
with the Geocoding API, the OAuth token must be assigned the correct scope.
Geocoding API supports the following scopes for use with forward geocoding:

- `https://www.googleapis.com/auth/maps-platform.geocode` --- Use with all Geocoding API methods.
- `https://www.googleapis.com/auth/maps-platform.geocode.address` --- Use only with `GeocodeAddress` for forward geocoding.

Also, you can use the general `https://www.googleapis.com/auth/cloud-platform`
scope for all Geocoding API methods. That scope is useful during
development, but not production, because it is a general scope that allows
access to all methods.

For more information and examples, see
[Use OAuth](https://developers.google.com/maps/documentation/geocoding/oauth-token).

## Geocode response

Geocoding returns a
[`GeocodeAddressResponse`](https://developers.google.com/maps/documentation/geocoding/reference/rest/v4/GeocodeAddressResponse)
object that contains the `results` array of
[`GeocodeResult`](https://developers.google.com/maps/documentation/geocoding/reference/rest/v4/GeocodeResult)
objects. Each `GeocodeResult` object represents a single place.

The Geocoding API responses include `types` arrays in two main places within the
[`GeocodeResult`](https://developers.google.com/maps/documentation/geocoding/reference/rest/v4/GeocodeResult):

1. **`GeocodeResult.types`** : This array indicates the overall type(s) of the result. The possible values are drawn from [Table A and Table B](https://developers.google.com/maps/documentation/places/web-service/place-types) on the Place Types (New) page.
2. **`GeocodeResult.addressComponents[].types`** : Each address component has a `types` array indicating the type of that specific part of the address. These values are drawn from the [Address types and address component types](https://developers.google.com/maps/documentation/places/web-service/place-types#address-types) table on the Place Types (New) page.

> [!NOTE]
> **Note:** Generally, only one entry in the `results` array is returned for address lookups, though the geocoder might return several results when address queries are ambiguous.

The complete JSON object is in the form:

```json
{
  "results": [
    {
      "place": "//places.googleapis.com/places/ChIJF4Yf2Ry7j4AR__1AkytDyAE",
      "placeId": "ChIJF4Yf2Ry7j4AR__1AkytDyAE",
      "location": {
        "latitude": 37.422010799999995,
        "longitude": -122.08474779999999
      },
      "granularity": "ROOFTOP",
      "viewport": {
        "low": {
          "latitude": 37.420656719708511,
          "longitude": -122.08547523029148
        },
        "high": {
          "latitude": 37.4233546802915,
          "longitude": -122.0827772697085
        }
      },
      "formattedAddress": "1600 Amphitheatre Pkwy, Mountain View, CA 94043, USA",
      "postalAddress": {
        "regionCode": "US",
        "languageCode": "en",
        "postalCode": "94043",
        "administrativeArea": "CA",
        "locality": "Mountain View",
        "addressLines": [
          "1600 Amphitheatre Pkwy"
        ]
      },
      "addressComponents": [
        {
          "longText": "1600",
          "shortText": "1600",
          "types": [
            "street_number"
          ]
        },
        {
          "longText": "Amphitheatre Parkway",
          "shortText": "Amphitheatre Pkwy",
          "types": [
            "route"
          ],
          "languageCode": "en"
        },
        ...
      ],
      "types": [
        "street_address"
      ],
      "plusCode": {
        "globalCode": "849VCWC8+R4",
        "compoundCode": "CWC8+R4 Mountain View, CA, USA"
      }
    }
  ]
}
```

## Required parameters

- `address` --- The street address or [Plus Code](https://plus.codes) that you want to geocode. **Note:** Address geocoding does not resolve latitude and longitude coordinates, or other unstructured strings that don't represent an address or a Plus Code. See [Pass an unstructured address string](https://developers.google.com/maps/documentation/geocoding/geocoding#pass_an_unstructured_address_string) for more details and examples of unsupported queries. Specify addresses in accordance with the format used by the national postal service of the country concerned. Additional address elements such as business names and unit, suite or floor numbers should be avoided. Street address elements should be delimited by spaces URL-encoded to `%20`. For example, pass the address "24 Sussex Drive Ottawa ON" as:

  ```
  24%20Sussex%20Drive%20Ottawa%20ON
  ```
  Format Plus Codes as shown below. Plus signs are URL-encoded to `%2B` and spaces are URL-encoded to `%20`:
  - A **global code** is a 4 character area code and 6 character or longer local code. For example, encode "849VCWC8+R9" as `849VCWC8%2BR9`.
  - A **compound code** is a 6 character or longer local code with an explicit location. For example, encode "CWC8+R9 Mountain View, CA, USA" as `CWC8%2BR9%20Mountain%20View%20CA%20USA`.

## Optional parameters

-

  ### locationBias

  Specifies an area to search as a
  [`Viewport`](https://developers.google.com/maps/documentation/geocoding/reference/rest/v4/Viewport).
  This location serves as a bias which means
  results around the specified location can be returned, including results
  near but outside of the area.

  > [!NOTE]
  > **Note:** The `locationBias` parameter can be overridden if the requested address contains an explicit location such as `Barcelona, Spain`. In this case, `locationBias` is ignored.

  Specify the region as a **rectangular Viewport**. A rectangle is a
  latitude-longitude viewport, represented as two
  diagonally opposite low and high points. The low point marks the southwest
  corner of the rectangle, and the high point represents the northeast
  corner of the rectangle.

  A viewport is considered a
  closed region, meaning it includes its boundary. The latitude bounds
  must range between -90 to 90 degrees inclusive, and the longitude bounds
  must range between -180 to 180 degrees inclusive:
  - If `low` = `high`, the viewport consists of that single point.
  - If `low.longitude` \> `high.longitude`, the longitude range is inverted (the viewport crosses the 180 degree longitude line).
  - If `low.longitude` = -180 degrees and `high.longitude` = 180 degrees, the viewport includes all longitudes.
  - If `low.longitude` = 180 degrees and `high.longitude` = -180 degrees, the longitude range is empty.
  - If `low.latitude` \> `high.latitude`, the latitude range is empty.

  Both low and high must be populated, and the represented box cannot be
  empty. An empty viewport results in an error.

  For example, this query string defines a viewport that fully encloses New York City:

  ```javascript
  ?locationBias.rectangle.low.latitude=40.477398&locationBias.rectangle.low.longitude=-74.259087&locationBias.rectangle.high.latitude=40.91618&locationBias.rectangle.high.longitude=-73.70018
  ```
-

  ### languageCode

  The language in which to return results.
  - See the [list of supported languages](https://developers.google.com/maps/faq#languagesupport). Google often updates the supported languages, so this list may not be exhaustive.
  - If `languageCode` is not supplied, the API defaults to `en`. If you specify an invalid language code, the API returns an `INVALID_ARGUMENT` error.
  - The API does its best to provide a street address that is readable for both the user and locals. To achieve that goal, it returns street addresses in the local language, transliterated to a script readable by the user if necessary, observing the preferred language. All other addresses are returned in the preferred language. Address components are all returned in the same language, which is chosen from the first component.
  - If a name is not available in the preferred language, the API uses the closest match.
  - The preferred language has a small influence on the set of results that the API chooses to return, and the order in which they are returned. The geocoder interprets abbreviations differently depending on language, such as the abbreviations for street types, or synonyms that may be valid in one language but not in another.
-

  ### regionCode

  The region code as a
  [two-character CLDR code](https://www.unicode.org/cldr/charts/latest/supplemental/territory_language_information.html) value. There is no default value. Most CLDR codes are identical to ISO 3166-1 codes.

  When geocoding an address, *forward geodcoding* , this parameter can influence, but not
  fully restrict, results from the service to the specified region. When geocoding a location or a
  place, *reverse geocoding* or *place geocoding*, this parameter can be used to
  format the address. In all cases, this parameter can affect results based on applicable law.
-

  ### FieldMask

  Create a [response field mask](https://developers.google.com/maps/documentation/geocoding/choose-fields) to specify the fields to return in the response. Pass the response field mask to the method by using the URL parameter
  `$fields` or `fields`, or by using the HTTP header
  `X-Goog-FieldMask`. For example, the below request will return only the `placeID` field of the response.

  ```curl
  curl -X GET -H 'Content-Type: application/json' \
  -H 'X-Goog-FieldMask: results.placeId' \
  -H "X-Goog-Api-Key: API_KEY" \
  https://geocode.googleapis.com/v4/geocode/address/1600+Amphitheatre+Parkway,+Mountain+View,+CA
  ```
  The response is:

  ```json
  {
    "results": [
      {
        "placeId": "ChIJiSSC8QK6j4AR98Thup8mqTc"
      }
    ]
  }
  ```

  See [Choose fields to return](https://developers.google.com/maps/documentation/geocoding/choose-fields) for more details.

  > [!IMPORTANT]
  > **Important:** Field masking is a good design practice to ensure that you don't request unnecessary data, which helps to avoid unnecessary processing time.

## Location biasing

Use the `locationBias` parameter to instruct the Geocoding service
to prefer results within a given viewport (expressed as a bounding box).
The `locationBias` parameter defines the latitude/longitude coordinates
of the southwest and northeast corners of this bounding box.

> [!NOTE]
> **Note:** This parameter only adds a *bias* towards results within the viewport, and doesn't guarantee that result(s) are contained by it. The [Geocoding best practices article](https://developers.google.com/maps/documentation/geocoding/best-practices) provides some guidance regarding which API method best fits which use-case.

For example, a geocode request for the address "Washington" can return
results for Washington, D.C. and for the US state of Washington:

```html
https://geocode.googleapis.com/v4/geocode/address/Washington?key=API_KEY
```

The response is in the form:

```json
{
  "results": [
    {
      "place": "//places.googleapis.com/places/ChIJW-T2Wt7Gt4kRKl2I1CJFUsI",
      "placeId": "ChIJW-T2Wt7Gt4kRKl2I1CJFUsI",
      "location": {
        "latitude": 38.9071923,
        "longitude": -77.0368707
      },
      "granularity": "APPROXIMATE",
      "viewport": {
        "low": {
          "latitude": 38.7916449,
          "longitude": -77.119759
        },
        "high": {
          "latitude": 38.9958641,
          "longitude": -76.909393
        }
      },
      "bounds": {
        "low": {
          "latitude": 38.7916449,
          "longitude": -77.119759
        },
        "high": {
          "latitude": 38.9958641,
          "longitude": -76.909393
        }
      },
      "formattedAddress": "Washington, DC, USA",
      "addressComponents": [
        {
          "longText": "Washington",
          "shortText": "Washington",
          "types": [
            "locality",
            "political"
          ],
          "languageCode": "en"
        },
        ...
      ],
      "types": [
        "locality",
        "political"
      ]
    },
    {
      "place": "//places.googleapis.com/places/ChIJ-bDD5__lhVQRuvNfbGh4QpQ",
      "placeId": "ChIJ-bDD5__lhVQRuvNfbGh4QpQ",
      "location": {
        "latitude": 47.7510741,
        "longitude": -120.7401386
      },
      "granularity": "APPROXIMATE",
      "viewport": {
        "low": {
          "latitude": 45.543541,
          "longitude": -124.84897389999999
        },
        "high": {
          "latitude": 49.0024945,
          "longitude": -116.91607109999998
        }
      },
      "bounds": {
        "low": {
          "latitude": 45.543541,
          "longitude": -124.84897389999999
        },
        "high": {
          "latitude": 49.0024442,
          "longitude": -116.91607109999998
        }
      },
      "formattedAddress": "Washington, USA",
      "addressComponents": [
        {
          "longText": "Washington",
          "shortText": "WA",
          "types": [
            "administrative_area_level_1",
            "political"
          ],
          "languageCode": "en"
        },
      ...
      ],
      "types": [
        "administrative_area_level_1",
        "political"
      ]
    }
  ]
}
```

However, adding a `locationBias` parameter defining a bounding box around
the north-east part of the US results in this geocode returning only the city of
Washington, D.C.:

```html
https://geocode.googleapis.com/v4/geocode/address/Washington?locationBias.rectangle.low.latitude=36.47&locationBias.rectangle.low.longitude=-84.72&locationBias.rectangle.high.latitude=43.39&locationBias.rectangle.high.longitude=-65.90&key=API_KEY
```

## Region biasing

In a geocoding request, you can instruct the Geocoding service to return
results biased to a particular region by using the `regionCode`
parameter. This parameter takes a
[two-character CLDR code](https://www.unicode.org/cldr/charts/latest/supplemental/territory_language_information.html) value specifying the region bias. Most CLDR codes
are identical to ISO 3166-1 codes.

There is no default value for `regionCode`. For example, a geocode for "Toledo"
returns results for the US and for Spain:

```html
https://geocode.googleapis.com/v4/geocode/address/Toledo?key=API_KEY
```

Response:

```json
{
  "results": [
    {
      "place": "//places.googleapis.com/places/ChIJeU4e_C2HO4gRRcM6RZ_IPHw",
      "placeId": "ChIJeU4e_C2HO4gRRcM6RZ_IPHw",
      "location": {
        "latitude": 41.652805199999996,
        "longitude": -83.5378674
      },
      "granularity": "APPROXIMATE",
      "viewport": {
        "low": {
          "latitude": 41.579513,
          "longitude": -83.6944089
        },
        "high": {
          "latitude": 41.733036,
          "longitude": -83.4493851
        }
      },
      "bounds": {
        "low": {
          "latitude": 41.579513,
          "longitude": -83.6944089
        },
        "high": {
          "latitude": 41.733036,
          "longitude": -83.4493851
        }
      },
      "formattedAddress": "Toledo, OH, USA",
      "addressComponents": [
        {
          "longText": "Toledo",
          "shortText": "Toledo",
          "types": [
            "locality",
            "political"
          ],
          "languageCode": "en"
        },
        ...
      ],
      "types": [
        "locality",
        "political"
      ]
    },
    {
      "place": "//places.googleapis.com/places/ChIJkwyrlqwLag0RiQIn2fdIshM",
      "placeId": "ChIJkwyrlqwLag0RiQIn2fdIshM",
      "location": {
        "latitude": 39.8628296,
        "longitude": -4.0273067
      },
      "granularity": "APPROXIMATE",
      "viewport": {
        "low": {
          "latitude": 39.8116682,
          "longitude": -4.179933
        },
        "high": {
          "latitude": 39.9251319,
          "longitude": -3.8148935
        }
      },
      "bounds": {
        "low": {
          "latitude": 39.8116682,
          "longitude": -4.179933
        },
        "high": {
          "latitude": 39.9251319,
          "longitude": -3.8148935
        }
      },
      "formattedAddress": "Toledo, España",
      "addressComponents": [
        {
          "longText": "Toledo",
          "shortText": "Toledo",
          "types": [
            "administrative_area_level_4",
            "political"
          ],
          "languageCode": "es"
        },
        ...
      ],
      "types": [
        "administrative_area_level_4",
        "political"
      ]
    },
    ...
  ]
}
```

A geocoding request for "Toledo" with `regionCode=es` (Spain) only returns
results from Spain:

```html
https://geocode.googleapis.com/v4/geocode/address/Toledo?regionCode=es&key=API_KEY
```